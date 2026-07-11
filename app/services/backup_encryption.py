"""Verschlüsselung für Cloud-Backup-Kopien (Dropbox/WebDAV/Google Drive/OneDrive).

Zwei Modi, gemeinsames selbstbeschreibendes Envelope-Format:
- Schlüsselpaar: RSA-OAEP verschlüsselt einen frischen AES-Schlüssel je Backup,
  der private Schlüssel wird nie gespeichert (nur einmalig zum Download angeboten).
- Passwort: PBKDF2-HMAC-SHA256 leitet je Backup mit zufälligem Salt einen
  AES-Schlüssel aus dem gespeicherten Passwort ab.
In beiden Fällen wird der eigentliche Inhalt mit AES-256-GCM verschlüsselt
(authentifizierte Verschlüsselung - ein falscher Schlüssel/falsches Passwort
führt zu einem klar erkennbaren Fehler statt stillem Datenmüll).

Bewusst auf die Bibliothek "cryptography" gestützt statt eigenem Krypto-Code:
anders als z. B. der handgerollte PDF-/PNG-Code in services/pdf.py wäre ein
Fehler hier kein kaputtes Feature, sondern ein trügerisches Sicherheitsgefühl.
"""
import os
import struct

from cryptography.hazmat.primitives.asymmetric import rsa, padding as asym_padding
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.exceptions import InvalidTag

MAGIC = b"KGLKBKENC1"
MODE_KEYPAIR = 0x01
MODE_PASSWORD = 0x02
PBKDF2_ITERATIONS = 600_000
AES_KEY_LEN = 32  # AES-256


def generate_keypair():
    """Erzeugt ein neues RSA-2048-Schlüsselpaar.

    Gibt (private_pem_bytes, public_pem_bytes) zurück. Der Aufrufer ist dafür
    verantwortlich, den privaten Schlüssel NICHT zu speichern (nur einmalig
    zum Download anzubieten).
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def public_key_fingerprint(public_pem):
    """Kurzer, für Menschen vergleichbarer Fingerabdruck eines öffentlichen Schlüssels."""
    from hashlib import sha256
    digest = sha256(public_pem.encode("utf-8") if isinstance(public_pem, str) else public_pem).hexdigest()
    return ":".join(digest[i:i + 4] for i in range(0, 20, 4)).upper()


def encrypt_for_keypair(data, public_pem):
    """Verschlüsselt data (bytes) mit einem frischen AES-Schlüssel, der wiederum
    mit dem gegebenen RSA-öffentlichen Schlüssel (PEM, str oder bytes) verschlüsselt wird."""
    if isinstance(public_pem, str):
        public_pem = public_pem.encode("utf-8")
    public_key = serialization.load_pem_public_key(public_pem)

    aes_key = os.urandom(AES_KEY_LEN)
    encrypted_aes_key = public_key.encrypt(
        aes_key,
        asym_padding.OAEP(
            mgf=asym_padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    nonce = os.urandom(12)
    ciphertext = AESGCM(aes_key).encrypt(nonce, data, None)

    return (
        MAGIC
        + bytes([MODE_KEYPAIR])
        + struct.pack(">H", len(encrypted_aes_key))
        + encrypted_aes_key
        + nonce
        + ciphertext
    )


def encrypt_for_password(data, password):
    """Verschlüsselt data (bytes) mit einem aus password abgeleiteten AES-Schlüssel."""
    salt = os.urandom(16)
    aes_key = _derive_password_key(password, salt, PBKDF2_ITERATIONS)
    nonce = os.urandom(12)
    ciphertext = AESGCM(aes_key).encrypt(nonce, data, None)

    return (
        MAGIC
        + bytes([MODE_PASSWORD])
        + salt
        + struct.pack(">I", PBKDF2_ITERATIONS)
        + nonce
        + ciphertext
    )


def _derive_password_key(password, salt, iterations):
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=AES_KEY_LEN, salt=salt, iterations=iterations)
    return kdf.derive(password.encode("utf-8"))


def decrypt_backup(envelope, private_pem=None, password=None):
    """Entschlüsselt ein von encrypt_for_keypair/encrypt_for_password erzeugtes
    Envelope. Genau eines von private_pem (PEM, str oder bytes) oder password
    muss zum tatsächlich verwendeten Modus passen.

    Wirft ValueError mit einer verständlichen deutschen Meldung bei falschem
    Format, falschem Schlüssel/Passwort oder manipulierten Daten.
    """
    if not envelope.startswith(MAGIC):
        raise ValueError("Das ist keine verschlüsselte Kegelkasse-Sicherung (fehlende Kennung).")

    pos = len(MAGIC)
    mode = envelope[pos]
    pos += 1

    try:
        if mode == MODE_KEYPAIR:
            if not private_pem:
                raise ValueError("Diese Sicherung ist mit einem Schlüsselpaar verschlüsselt - bitte die private Schlüsseldatei hochladen.")
            if isinstance(private_pem, str):
                private_pem = private_pem.encode("utf-8")
            (key_len,) = struct.unpack(">H", envelope[pos:pos + 2])
            pos += 2
            encrypted_aes_key = envelope[pos:pos + key_len]
            pos += key_len

            try:
                private_key = serialization.load_pem_private_key(private_pem, password=None)
            except Exception as exc:
                raise ValueError("Die hochgeladene Datei ist kein gültiger privater Schlüssel.") from exc

            try:
                aes_key = private_key.decrypt(
                    encrypted_aes_key,
                    asym_padding.OAEP(
                        mgf=asym_padding.MGF1(algorithm=hashes.SHA256()),
                        algorithm=hashes.SHA256(),
                        label=None,
                    ),
                )
            except Exception as exc:
                raise ValueError("Der private Schlüssel passt nicht zu dieser Sicherung.") from exc

        elif mode == MODE_PASSWORD:
            if not password:
                raise ValueError("Diese Sicherung ist mit einem Passwort verschlüsselt - bitte das Passwort eingeben.")
            salt = envelope[pos:pos + 16]
            pos += 16
            (iterations,) = struct.unpack(">I", envelope[pos:pos + 4])
            pos += 4
            aes_key = _derive_password_key(password, salt, iterations)

        else:
            raise ValueError("Unbekanntes Verschlüsselungsformat - diese Sicherung wurde vermutlich mit einer neueren Kegelkasse-Version erstellt.")

        nonce = envelope[pos:pos + 12]
        pos += 12
        ciphertext = envelope[pos:]

        try:
            return AESGCM(aes_key).decrypt(nonce, ciphertext, None)
        except InvalidTag as exc:
            raise ValueError("Entschlüsselung fehlgeschlagen - falsches Passwort/falscher Schlüssel, oder die Datei ist beschädigt.") from exc

    except ValueError:
        raise
    except (struct.error, IndexError) as exc:
        raise ValueError("Die verschlüsselte Datei ist beschädigt oder unvollständig.") from exc
