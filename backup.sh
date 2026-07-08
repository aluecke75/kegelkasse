#!/bin/bash

echo
echo "========================================="
echo "   Kegelkasse Git Backup"
echo "========================================="
echo

git status

echo

if git diff-index --quiet HEAD --; then
    echo "Keine Änderungen vorhanden."
    exit 0
fi

echo
read -p "Commit-Nachricht [Backup]: " MESSAGE

if [ -z "$MESSAGE" ]; then
    MESSAGE="Backup $(date '+%Y-%m-%d %H:%M')"
fi

echo
echo "Änderungen übernehmen..."
git add .

echo
echo "Commit erstellen..."
git commit -m "$MESSAGE"

if [ $? -ne 0 ]; then
    echo
    echo "Kein Commit erstellt."
    exit 1
fi

echo
echo "Push zu GitHub..."
git push origin main

if [ $? -ne 0 ]; then
    echo
    echo "FEHLER beim Push zu GitHub!"
    exit 1
fi

echo
echo "Push zu Gitea..."
git push gitea main

if [ $? -ne 0 ]; then
    echo
    echo "FEHLER beim Push zu Gitea!"
    exit 1
fi

echo
echo "========================================="
echo "Backup erfolgreich."
echo "========================================="
echo

git log --oneline -5