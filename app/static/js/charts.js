/*
 * Leichte, abhängigkeitsfreie SVG-Diagramme für die Kegelkasse.
 * Unterstützt Linien- und (gruppierte) Balkendiagramme mit Legende,
 * Hover-Tooltip und Direktbeschriftung. Kein externes Chart-Framework,
 * damit die App weiterhin ohne zusätzliche Abhängigkeiten läuft.
 */
(function (global) {
    "use strict";

    var PALETTE = ["#2a78d6", "#1baf7a", "#eda100", "#4a3aa7"];
    var INK_PRIMARY = "#0b0b0b";
    var INK_MUTED = "#898781";
    var GRIDLINE = "#e1e0d9";
    var BASELINE = "#c3c2b7";

    var tooltipEl = null;
    function getTooltip() {
        if (!tooltipEl) {
            tooltipEl = document.createElement("div");
            tooltipEl.className = "kk-chart-tooltip";
            document.body.appendChild(tooltipEl);
        }
        return tooltipEl;
    }

    function showTooltip(evt, html) {
        var tip = getTooltip();
        tip.innerHTML = html;
        tip.style.display = "block";
        var x = evt.clientX + 14;
        var y = evt.clientY + 14;
        var maxX = window.innerWidth - tip.offsetWidth - 10;
        if (x > maxX) x = evt.clientX - tip.offsetWidth - 14;
        tip.style.left = x + "px";
        tip.style.top = y + "px";
    }

    function hideTooltip() {
        if (tooltipEl) tooltipEl.style.display = "none";
    }

    function el(tag, attrs) {
        var node = document.createElementNS("http://www.w3.org/2000/svg", tag);
        for (var key in attrs) {
            node.setAttribute(key, attrs[key]);
        }
        return node;
    }

    function niceMax(value) {
        if (value <= 0) return 1;
        var magnitude = Math.pow(10, Math.floor(Math.log10(value)));
        var normalized = value / magnitude;
        var step = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
        return step * magnitude;
    }

    function buildLegend(container, series) {
        if (series.length < 2) return;
        var legend = document.createElement("div");
        legend.className = "kk-chart-legend";
        series.forEach(function (s) {
            var item = document.createElement("span");
            item.className = "kk-chart-legend-item";
            item.innerHTML = '<span class="kk-chart-legend-dot" style="background:' + s.color + '"></span>' + s.name;
            legend.appendChild(item);
        });
        container.appendChild(legend);
    }

    function baseChart(containerId, opts) {
        var container = document.getElementById(containerId);
        if (!container) return null;
        container.innerHTML = "";
        container.classList.add("kk-chart-root");

        var width = 720;
        var height = opts.height || 260;
        var margin = { top: 16, right: 16, bottom: 30, left: 44 };
        var innerWidth = width - margin.left - margin.right;
        var innerHeight = height - margin.top - margin.bottom;

        var svg = el("svg", {
            viewBox: "0 0 " + width + " " + height,
            width: "100%",
            height: height,
            role: "img",
            "aria-label": opts.ariaLabel || opts.title || "Diagramm",
        });

        return { container: container, svg: svg, width: width, height: height, margin: margin, innerWidth: innerWidth, innerHeight: innerHeight };
    }

    function drawAxes(chart, xLabels, maxValue, formatValue) {
        var svg = chart.svg, margin = chart.margin, innerWidth = chart.innerWidth, innerHeight = chart.innerHeight;

        // Y-Gitterlinien (4 Schritte)
        var steps = 4;
        for (var i = 0; i <= steps; i++) {
            var yValue = (maxValue / steps) * i;
            var y = margin.top + innerHeight - (innerHeight * (yValue / maxValue || 0));
            svg.appendChild(el("line", {
                x1: margin.left, x2: margin.left + innerWidth, y1: y, y2: y,
                stroke: GRIDLINE, "stroke-width": 1,
            }));
            var label = el("text", {
                x: margin.left - 8, y: y + 3, "text-anchor": "end",
                "font-size": 10, fill: INK_MUTED, "font-family": "system-ui, -apple-system, sans-serif",
            });
            label.textContent = formatValue(yValue);
            svg.appendChild(label);
        }

        // Baseline
        svg.appendChild(el("line", {
            x1: margin.left, x2: margin.left + innerWidth, y1: margin.top + innerHeight, y2: margin.top + innerHeight,
            stroke: BASELINE, "stroke-width": 1,
        }));

        // X-Beschriftungen
        var stepX = innerWidth / Math.max(xLabels.length - 1, 1);
        var everyNth = Math.ceil(xLabels.length / 10);
        xLabels.forEach(function (label, idx) {
            if (idx % everyNth !== 0 && idx !== xLabels.length - 1) return;
            var x = xLabels.length === 1 ? margin.left + innerWidth / 2 : margin.left + stepX * idx;
            var text = el("text", {
                x: x, y: margin.top + innerHeight + 18, "text-anchor": "middle",
                "font-size": 10, fill: INK_MUTED, "font-family": "system-ui, -apple-system, sans-serif",
            });
            text.textContent = label;
            svg.appendChild(text);
        });
    }

    function renderLineChart(containerId, opts) {
        var series = opts.series || [];
        var xLabels = opts.xLabels || [];
        var formatValue = opts.formatValue || function (v) { return String(Math.round(v)); };
        var chart = baseChart(containerId, opts);
        if (!chart) return;

        series.forEach(function (s, idx) {
            if (!s.color) s.color = PALETTE[idx % PALETTE.length];
        });

        var maxValue = niceMax(Math.max.apply(null, series.reduce(function (acc, s) {
            return acc.concat(s.points.map(function (p) { return p.y; }));
        }, [0])));

        drawAxes(chart, xLabels, maxValue, formatValue);

        var margin = chart.margin, innerWidth = chart.innerWidth, innerHeight = chart.innerHeight;
        var stepX = innerWidth / Math.max(xLabels.length - 1, 1);

        series.forEach(function (s) {
            var pathPoints = s.points.map(function (p, idx) {
                var x = xLabels.length === 1 ? margin.left + innerWidth / 2 : margin.left + stepX * idx;
                var y = margin.top + innerHeight - (innerHeight * (p.y / maxValue || 0));
                return { x: x, y: y, raw: p };
            });

            var d = pathPoints.map(function (pt, idx) {
                return (idx === 0 ? "M " : "L ") + pt.x.toFixed(1) + " " + pt.y.toFixed(1);
            }).join(" ");

            chart.svg.appendChild(el("path", {
                d: d, fill: "none", stroke: s.color, "stroke-width": 2,
                "stroke-linecap": "round", "stroke-linejoin": "round",
            }));

            pathPoints.forEach(function (pt) {
                var circle = el("circle", { cx: pt.x, cy: pt.y, r: 4, fill: s.color, stroke: "#fcfcfb", "stroke-width": 1.5 });
                circle.style.cursor = "pointer";
                circle.addEventListener("mousemove", function (evt) {
                    showTooltip(evt, "<strong>" + (pt.raw.label || "") + "</strong><br>" + s.name + ": " + formatValue(pt.raw.y));
                });
                circle.addEventListener("mouseleave", hideTooltip);
                chart.svg.appendChild(circle);
            });

            // Direktbeschriftung am letzten Punkt
            if (pathPoints.length) {
                var last = pathPoints[pathPoints.length - 1];
                var directLabel = el("text", {
                    x: last.x + 6, y: last.y - 8, "font-size": 10, "font-weight": 700,
                    fill: s.color, "font-family": "system-ui, -apple-system, sans-serif",
                });
                directLabel.textContent = formatValue(last.raw.y);
                chart.svg.appendChild(directLabel);
            }
        });

        chart.container.appendChild(chart.svg);
        buildLegend(chart.container, series);
    }

    function renderBarChart(containerId, opts) {
        var groups = opts.groups || [];
        var series = opts.series || [];
        var formatValue = opts.formatValue || function (v) { return String(Math.round(v)); };
        var chart = baseChart(containerId, opts);
        if (!chart) return;

        series.forEach(function (s, idx) {
            if (!s.color) s.color = PALETTE[idx % PALETTE.length];
        });

        var maxValue = niceMax(Math.max.apply(null, series.reduce(function (acc, s) {
            return acc.concat(s.values);
        }, [0])));

        drawAxes(chart, groups, maxValue, formatValue);

        var margin = chart.margin, innerWidth = chart.innerWidth, innerHeight = chart.innerHeight;
        var groupWidth = innerWidth / Math.max(groups.length, 1);
        var barGap = 3;
        var barWidth = Math.min(38, (groupWidth - barGap * (series.length + 1)) / Math.max(series.length, 1));

        groups.forEach(function (groupLabel, groupIdx) {
            var groupX = margin.left + groupWidth * groupIdx;
            var totalBarsWidth = barWidth * series.length + barGap * (series.length - 1);
            var startX = groupX + (groupWidth - totalBarsWidth) / 2;

            series.forEach(function (s, sIdx) {
                var value = s.values[groupIdx] || 0;
                var barHeight = innerHeight * (value / maxValue || 0);
                var x = startX + sIdx * (barWidth + barGap);
                var y = margin.top + innerHeight - barHeight;

                var rect = el("rect", {
                    x: x, y: y, width: barWidth, height: Math.max(barHeight, 0),
                    fill: s.color, rx: 4, ry: 4,
                });
                rect.style.cursor = "pointer";
                rect.addEventListener("mousemove", function (evt) {
                    showTooltip(evt, "<strong>" + groupLabel + "</strong><br>" + s.name + ": " + formatValue(value));
                });
                rect.addEventListener("mouseleave", hideTooltip);
                chart.svg.appendChild(rect);
            });
        });

        chart.container.appendChild(chart.svg);
        buildLegend(chart.container, series);
    }

    global.KegelCharts = {
        renderLineChart: renderLineChart,
        renderBarChart: renderBarChart,
    };
})(window);
