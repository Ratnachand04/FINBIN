const API_BASE = `${window.location.origin}/api/v1`;

const state = {
    coin: "BTC",
    interval: "15m",
    refreshSeconds: 15,
    refreshTimer: null,
    availableCoins: ["BTC", "ETH", "DOGE"],
    liveTicksByCoin: {},
    latestSymbols: [],
    ws: {
        socket: null,
        retryTimer: null,
        connected: false,
        reconnectDelayMs: 1500,
        maxReconnectDelayMs: 15000,
    },
    charts: {
        price: null,
        candleSeries: null,
        volumeSeries: null,
        liveSeries: null,
        confidence: null,
        confidenceSeries: null,
        miniByCoin: {},
    },
};

state.availableCoins.forEach((coin) => {
    state.liveTicksByCoin[coin] = [];
});

function switchView(viewName, buttonEl) {
    document.querySelectorAll(".nav-item").forEach((el) => el.classList.remove("active"));
    document.querySelectorAll(".view-section").forEach((el) => el.classList.remove("active"));

    if (buttonEl) {
        buttonEl.classList.add("active");
    }

    const target = document.getElementById(`view-${viewName}`);
    if (target) {
        target.classList.add("active");
    }

    if (viewName === "charts") {
        initPriceChart();
        initConfidenceChart();
    }

    if (viewName === "signals") {
        loadSignalHistory();
    }

    if (viewName === "models") {
        loadRedditFeed();
    }
}

function handleCoinChange() {
    const selected = document.getElementById("global-coin-select");
    if (!selected) {
        return;
    }

    const normalized = normalizeCoinSymbol(selected.value);
    if (!normalized) {
        return;
    }

    state.coin = normalized;
    selected.value = normalized;
    initPriceChart();
    initConfidenceChart();
    manualRefresh();
}

function selectCoinFromCard(coin) {
    const normalized = normalizeCoinSymbol(coin);
    if (!normalized) {
        return;
    }

    state.coin = normalized;
    renderCoinSelector();
    initPriceChart();
    initConfidenceChart();
    manualRefresh();
}

function handleIntervalChange() {
    const selected = document.getElementById("global-interval-select");
    if (!selected) {
        return;
    }

    state.interval = selected.value;
    initPriceChart();
}

function updateRefreshLabel(value) {
    const refreshValue = document.getElementById("refresh-value");
    if (refreshValue) {
        refreshValue.textContent = `${value}s`;
    }
}

function applyRefreshInterval() {
    const slider = document.getElementById("refresh-seconds");
    if (!slider) {
        return;
    }

    const parsed = Number.parseInt(slider.value, 10);
    if (!Number.isFinite(parsed) || parsed <= 0) {
        return;
    }

    state.refreshSeconds = parsed;
    restartRefreshTimer();
    updateRefreshLabel(parsed);
}

async function apiFetch(path, options = {}) {
    const requestOptions = {
        method: options.method || "GET",
        headers: {
            ...(options.headers || {}),
        },
    };

    if (options.body !== undefined) {
        requestOptions.body = options.body;
        if (!(options.body instanceof FormData)) {
            requestOptions.headers["Content-Type"] = "application/json";
        }
    }

    const response = await fetch(`${API_BASE}${path}`, requestOptions);
    if (!response.ok) {
        throw new Error(`Request failed (${response.status}) for ${path}`);
    }

    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
        return response.json();
    }

    return null;
}

async function safeFetch(path, options = {}) {
    try {
        return await apiFetch(path, options);
    } catch (error) {
        console.warn(`[BINFIN UI] ${path}:`, error.message || error);
        return null;
    }
}

function setStatus(healthPayload) {
    const statusEl = document.getElementById("sys-status");
    const dotEl = document.getElementById("status-dot");

    const status = (healthPayload && healthPayload.status) ? String(healthPayload.status).toUpperCase() : "OFFLINE";
    const isOnline = status === "OK" || status === "ONLINE";
    const isDegraded = status === "DEGRADED";

    if (statusEl) {
        statusEl.textContent = isOnline ? "ONLINE" : status;
    }

    if (dotEl) {
        dotEl.classList.remove("online", "offline", "degraded");
        if (isOnline) {
            dotEl.classList.add("online");
        } else if (isDegraded) {
            dotEl.classList.add("degraded");
        } else {
            dotEl.classList.add("offline");
        }
    }
}

function setLastRefresh() {
    const refreshEl = document.getElementById("last-refresh");
    if (!refreshEl) {
        return;
    }
    refreshEl.textContent = `Last refresh: ${new Date().toLocaleTimeString()}`;
}

function toPercent(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
        return "0.00%";
    }
    return `${(Number(value) * 100).toFixed(2)}%`;
}

function toPrice(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
        return "-";
    }
    const n = Number(value);
    if (Math.abs(n) >= 1000) {
        return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
    }
    return n.toLocaleString(undefined, { maximumFractionDigits: 6 });
}

function toSignedPct(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
        return "0.00%";
    }
    const n = Number(value);
    return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}

function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => {
        if (char === "&") {
            return "&amp;";
        }
        if (char === "<") {
            return "&lt;";
        }
        if (char === ">") {
            return "&gt;";
        }
        if (char === '"') {
            return "&quot;";
        }
        return "&#39;";
    });
}

function normalizeCoinSymbol(value) {
    const raw = String(value || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
    if (!raw) {
        return "";
    }
    return raw.endsWith("USDT") ? raw.slice(0, -4) : raw;
}

function coinDomId(coin) {
    return normalizeCoinSymbol(coin);
}

function ensureCoinBucket(coin) {
    const normalized = normalizeCoinSymbol(coin);
    if (!normalized) {
        return "";
    }
    if (!Array.isArray(state.liveTicksByCoin[normalized])) {
        state.liveTicksByCoin[normalized] = [];
    }
    return normalized;
}

function renderCoinSelector() {
    const selector = document.getElementById("global-coin-select");
    if (!selector) {
        return;
    }

    const selectedBefore = state.coin;
    selector.innerHTML = state.availableCoins.map((coin) => (
        `<option value="${coin}">${coin} / USDT</option>`
    )).join("");

    if (state.availableCoins.includes(selectedBefore)) {
        selector.value = selectedBefore;
        return;
    }

    if (state.availableCoins.length) {
        state.coin = state.availableCoins[0];
        selector.value = state.coin;
    }
}

function setAvailableCoins(coins) {
    if (!Array.isArray(coins) || !coins.length) {
        return;
    }

    const merged = [...state.availableCoins];
    const known = new Set(merged);
    let changed = false;

    coins.forEach((coinRaw) => {
        const coin = normalizeCoinSymbol(coinRaw);
        if (!coin) {
            return;
        }
        ensureCoinBucket(coin);
        if (known.has(coin)) {
            return;
        }
        known.add(coin);
        merged.push(coin);
        changed = true;
    });

    const caption = document.getElementById("all-coins-caption");
    if (!changed) {
        if (caption) {
            caption.textContent = `Streaming ${state.availableCoins.length} extracted coin(s) from the live feed`;
        }
        return;
    }

    merged.sort((a, b) => a.localeCompare(b));
    state.availableCoins = merged;

    if (!state.availableCoins.includes(state.coin) && state.availableCoins.length) {
        state.coin = state.availableCoins[0];
    }

    renderCoinSelector();

    if (caption) {
        caption.textContent = `Streaming ${state.availableCoins.length} extracted coin(s) from the live feed`;
    }
}

async function loadCoinUniverse() {
    const configuredCoins = await safeFetch("/coins/?is_active=true&page_size=200");
    if (Array.isArray(configuredCoins) && configuredCoins.length) {
        setAvailableCoins(configuredCoins.map((row) => row.symbol));
        return;
    }

    const predictedRows = await safeFetch("/predictions/?hours=24&limit=500");
    if (Array.isArray(predictedRows) && predictedRows.length) {
        setAvailableCoins(predictedRows.map((row) => row.symbol));
    }
}

function pulseElement(element) {
    if (!element) {
        return;
    }
    element.classList.remove("flash-update");
    void element.offsetWidth;
    element.classList.add("flash-update");
}

function updateOverview(activeSignals, outcomes, coinDetails, runtime) {
    const metricSignals = document.getElementById("metric-signals");
    const metricWin = document.getElementById("metric-winrate");
    const metricBest = document.getElementById("metric-best");
    const metricPrice = document.getElementById("metric-price");
    const runtimeSummary = document.getElementById("runtime-summary");

    const activeList = Array.isArray(activeSignals) ? activeSignals : [];
    const outcomesList = Array.isArray(outcomes) ? outcomes : [];

    if (metricSignals) {
        metricSignals.textContent = String(activeList.length);
    }

    const pair = `${state.coin}USDT`;
    const scopedOutcomes = outcomesList.filter((row) => String(row.symbol || "").toUpperCase() === pair);
    const relevantOutcomes = scopedOutcomes.length ? scopedOutcomes : outcomesList;

    let wins = 0;
    let total = 0;
    let best = null;

    relevantOutcomes.forEach((row) => {
        const pnl = Number(row.pnl_pct ?? 0);
        total += 1;
        if (pnl > 0) {
            wins += 1;
        }
        if (best === null || pnl > best.pnl) {
            best = { pnl, symbol: String(row.symbol || "N/A") };
        }
    });

    if (metricWin) {
        metricWin.textContent = `${total ? ((wins / total) * 100).toFixed(2) : "0.00"}%`;
    }

    if (metricBest) {
        if (best) {
            metricBest.textContent = `${best.symbol} ${toSignedPct(best.pnl)}`;
        } else {
            metricBest.textContent = "N/A";
        }
    }

    if (metricPrice) {
        const currentPrice = coinDetails && coinDetails.current_price !== undefined
            ? coinDetails.current_price
            : null;
        metricPrice.textContent = toPrice(currentPrice);
    }

    if (runtimeSummary) {
        const runtimeDevice = runtime && runtime.selected_device ? String(runtime.selected_device).toUpperCase() : "UNKNOWN";
        const cuda = runtime && runtime.cuda_available ? "YES" : "NO";
        const ollama = runtime && runtime.ollama_reachable ? "YES" : "NO";
        const change = coinDetails && coinDetails.change_24h_pct !== undefined
            ? toSignedPct(Number(coinDetails.change_24h_pct))
            : "N/A";

        runtimeSummary.innerHTML = [
            `<span><strong>Asset:</strong> ${state.coin} (${state.interval})</span>`,
            `<span><strong>24h Change:</strong> ${change}</span>`,
            `<span><strong>Runtime:</strong> ${runtimeDevice}</span>`,
            `<span><strong>CUDA:</strong> ${cuda}</span>`,
            `<span><strong>Ollama:</strong> ${ollama}</span>`,
        ].join(" ");
    }
}

function renderRecentSignals(signals) {
    const tbody = document.querySelector("#recent-signals-table tbody");
    if (!tbody) {
        return;
    }

    const rows = Array.isArray(signals) ? signals.slice(0, 15) : [];
    if (!rows.length) {
        tbody.innerHTML = "<tr><td colspan='4'>No live signals available.</td></tr>";
        return;
    }

    tbody.innerHTML = rows.map((row) => {
        const signal = String(row.signal || "HOLD").toUpperCase();
        const confidence = Number(row.confidence ?? 0);
        const colorClass = signal === "BUY" ? "tag-buy" : signal === "SELL" ? "tag-sell" : "tag-hold";
        const ts = row.ts ? new Date(row.ts).toLocaleTimeString() : "-";

        return `
            <tr class="row-enter">
                <td><strong>${row.symbol || "-"}</strong></td>
                <td><span class="tag ${colorClass}">${signal}</span></td>
                <td>${(confidence * 100).toFixed(2)}%</td>
                <td>${ts}</td>
            </tr>
        `;
    }).join("");
}

function renderSignalHistory(signals) {
    const tbody = document.querySelector("#signal-history-table tbody");
    if (!tbody) {
        return;
    }

    const rows = Array.isArray(signals) ? signals.slice(0, 150) : [];
    if (!rows.length) {
        tbody.innerHTML = "<tr><td colspan='5'>No signal history available.</td></tr>";
        return;
    }

    tbody.innerHTML = rows.map((row) => {
        const ts = row.ts ? new Date(row.ts).toLocaleString() : "-";
        const signal = String(row.signal || "HOLD").toUpperCase();
        const colorClass = signal === "BUY" ? "tag-buy" : signal === "SELL" ? "tag-sell" : "tag-hold";
        return `
            <tr>
                <td>${ts}</td>
                <td>${row.symbol || "-"}</td>
                <td><span class="tag ${colorClass}">${signal}</span></td>
                <td>${toPercent(row.confidence || 0)}</td>
                <td>${Number(row.strength ?? 0).toFixed(2)}</td>
            </tr>
        `;
    }).join("");
}

function renderRedditFeed(items) {
    const container = document.getElementById("reddit-live-feed");
    if (!container) {
        return;
    }

    const rows = Array.isArray(items) ? items.slice(0, 20) : [];
    if (!rows.length) {
        container.innerHTML = '<div class="reddit-item">No recent Reddit items for this coin.</div>';
        return;
    }

    container.innerHTML = rows.map((item) => {
        const subreddit = escapeHtml(item.subreddit || "unknown");
        const title = escapeHtml(item.title || "Untitled post");
        const body = escapeHtml(String(item.body || "").replace(/\s+/g, " ").slice(0, 220));
        const mentions = Array.isArray(item.mentioned_coins) && item.mentioned_coins.length
            ? item.mentioned_coins.map((coin) => escapeHtml(coin)).join(", ")
            : escapeHtml(state.coin);
        const ts = item.ts ? new Date(item.ts).toLocaleString() : "-";
        const score = Number(item.score ?? 0);
        const comments = Number(item.num_comments ?? 0);
        const link = item.url
            ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">open</a>`
            : "";

        return `
            <article class="reddit-item">
                <div class="reddit-item-header">
                    <strong>r/${subreddit}</strong>
                    <span>${escapeHtml(ts)}</span>
                </div>
                <h4>${title}</h4>
                <p>${body}</p>
                <div class="reddit-meta">
                    <span>score ${score}</span>
                    <span>comments ${comments}</span>
                    <span>${mentions}</span>
                    ${link}
                </div>
            </article>
        `;
    }).join("");
}

function updateRuntimePanel(runtime) {
    const deviceEl = document.getElementById("runtime-device");
    const cudaEl = document.getElementById("runtime-cuda");
    const ollamaEl = document.getElementById("runtime-ollama");

    if (deviceEl) {
        deviceEl.textContent = runtime && runtime.selected_device ? String(runtime.selected_device).toUpperCase() : "N/A";
    }
    if (cudaEl) {
        cudaEl.textContent = runtime && runtime.cuda_available ? "YES" : "NO";
    }
    if (ollamaEl) {
        ollamaEl.textContent = runtime && runtime.ollama_reachable ? "YES" : "NO";
    }
}

function ensurePriceChart() {
    if (state.charts.price && state.charts.candleSeries && state.charts.volumeSeries && state.charts.liveSeries) {
        return;
    }

    const container = document.getElementById("tvchart");
    if (!container || typeof LightweightCharts === "undefined") {
        return;
    }

    const chart = LightweightCharts.createChart(container, {
        layout: {
            background: { color: "transparent" },
            textColor: "#d5f2ff",
        },
        grid: {
            vertLines: { color: "rgba(255,255,255,0.08)" },
            horzLines: { color: "rgba(255,255,255,0.08)" },
        },
        rightPriceScale: {
            borderColor: "rgba(255,255,255,0.2)",
        },
        timeScale: {
            borderColor: "rgba(255,255,255,0.2)",
            timeVisible: true,
            secondsVisible: false,
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal,
        },
    });

    const candleSeries = chart.addCandlestickSeries({
        upColor: "#06d6a0",
        downColor: "#ef476f",
        borderUpColor: "#06d6a0",
        borderDownColor: "#ef476f",
        wickUpColor: "#06d6a0",
        wickDownColor: "#ef476f",
    });

    const volumeSeries = chart.addHistogramSeries({
        color: "rgba(0, 209, 178, 0.35)",
        priceFormat: { type: "volume" },
        priceScaleId: "",
        scaleMargins: {
            top: 0.82,
            bottom: 0,
        },
    });

    const liveSeries = chart.addLineSeries({
        color: "#32b8ff",
        lineWidth: 2,
        crosshairMarkerVisible: true,
        lastValueVisible: true,
        priceLineVisible: false,
    });

    const observer = new ResizeObserver((entries) => {
        const rect = entries[0] && entries[0].contentRect;
        if (rect) {
            chart.applyOptions({ width: rect.width, height: rect.height });
        }
    });
    observer.observe(container);

    state.charts.price = chart;
    state.charts.candleSeries = candleSeries;
    state.charts.volumeSeries = volumeSeries;
    state.charts.liveSeries = liveSeries;
}

async function initPriceChart() {
    ensurePriceChart();

    if (!state.charts.candleSeries || !state.charts.volumeSeries || !state.charts.liveSeries) {
        return;
    }

    const title = document.getElementById("chart-title");
    if (title) {
        title.textContent = `${state.coin}USDT candlesticks (${state.interval})`;
    }

    const payload = await safeFetch(`/coins/${state.coin}/price-history?interval=${state.interval}&limit=500`);
    if (!Array.isArray(payload) || !payload.length) {
        state.charts.candleSeries.setData([]);
        state.charts.volumeSeries.setData([]);
        state.charts.liveSeries.setData([]);
        return;
    }

    const rows = payload
        .map((row) => ({
            time: Math.floor(new Date(row.ts).getTime() / 1000),
            open: Number(row.open),
            high: Number(row.high),
            low: Number(row.low),
            close: Number(row.close),
            volume: Number(row.volume || 0),
        }))
        .filter((row) => Number.isFinite(row.time) && Number.isFinite(row.open) && Number.isFinite(row.close))
        .sort((a, b) => a.time - b.time);

    state.charts.candleSeries.setData(rows.map((row) => ({
        time: row.time,
        open: row.open,
        high: row.high,
        low: row.low,
        close: row.close,
    })));

    state.charts.volumeSeries.setData(rows.map((row) => ({
        time: row.time,
        value: row.volume,
        color: row.close >= row.open ? "rgba(6,214,160,0.45)" : "rgba(239,71,111,0.45)",
    })));

    const historicalLine = rows.slice(-180).map((row) => ({ time: row.time, value: row.close }));
    const liveLine = state.liveTicksByCoin[state.coin] || [];
    state.charts.liveSeries.setData(liveLine.length ? liveLine : historicalLine);

    if (state.charts.price) {
        state.charts.price.timeScale().fitContent();
    }
}

function ensureConfidenceChart() {
    if (state.charts.confidence && state.charts.confidenceSeries) {
        return;
    }

    const container = document.getElementById("confidencechart");
    if (!container || typeof LightweightCharts === "undefined") {
        return;
    }

    const chart = LightweightCharts.createChart(container, {
        layout: {
            background: { color: "transparent" },
            textColor: "#d5f2ff",
        },
        grid: {
            vertLines: { color: "rgba(255,255,255,0.08)" },
            horzLines: { color: "rgba(255,255,255,0.08)" },
        },
        rightPriceScale: {
            borderColor: "rgba(255,255,255,0.2)",
        },
        timeScale: {
            borderColor: "rgba(255,255,255,0.2)",
            timeVisible: true,
            secondsVisible: false,
        },
    });

    const lineSeries = chart.addAreaSeries({
        lineColor: "#00d1b2",
        topColor: "rgba(0,209,178,0.35)",
        bottomColor: "rgba(0,209,178,0.02)",
        lineWidth: 2,
    });

    const observer = new ResizeObserver((entries) => {
        const rect = entries[0] && entries[0].contentRect;
        if (rect) {
            chart.applyOptions({ width: rect.width, height: rect.height });
        }
    });
    observer.observe(container);

    state.charts.confidence = chart;
    state.charts.confidenceSeries = lineSeries;
}

async function initConfidenceChart(predictionsPayload = null) {
    ensureConfidenceChart();
    if (!state.charts.confidenceSeries) {
        return;
    }

    let predictions = predictionsPayload;
    if (!Array.isArray(predictions)) {
        predictions = await safeFetch(`/predictions/?symbol=${state.coin}USDT&hours=72&limit=300`);
    }

    if (!Array.isArray(predictions) || !predictions.length) {
        state.charts.confidenceSeries.setData([]);
        return;
    }

    const rows = predictions
        .map((row) => ({
            time: Math.floor(new Date(row.ts).getTime() / 1000),
            value: Number(row.confidence || 0) * 100,
        }))
        .filter((row) => Number.isFinite(row.time) && Number.isFinite(row.value))
        .sort((a, b) => a.time - b.time);

    state.charts.confidenceSeries.setData(rows);
    if (state.charts.confidence) {
        state.charts.confidence.timeScale().fitContent();
    }
}

function buildWebSocketUrl(path) {
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    return `${protocol}://${window.location.host}${path}`;
}

function updateWsBadge(connected, message) {
    const dot = document.getElementById("ws-dot");
    const label = document.getElementById("ws-label");
    const badge = document.getElementById("ws-badge");

    if (dot) {
        dot.classList.remove("online", "offline");
        dot.classList.add(connected ? "online" : "offline");
    }
    if (label) {
        label.textContent = message;
    }
    if (badge) {
        badge.classList.toggle("connected", Boolean(connected));
    }
}

function renderTickerTape(symbolRows) {
    const tape = document.getElementById("ticker-tape");
    if (!tape) {
        return;
    }

    if (!Array.isArray(symbolRows) || !symbolRows.length) {
        tape.innerHTML = '<span class="ticker-item">Waiting for market feed...</span>';
        return;
    }

    const itemHtml = symbolRows.map((row) => {
        const symbol = escapeHtml(row.symbol || "-");
        const price = toPrice(row.price);
        return `<span class="ticker-item"><strong>${symbol}</strong> ${price}</span>`;
    }).join("");

    tape.innerHTML = `${itemHtml}${itemHtml}`;
}

function signalTagClass(signal) {
    const normalized = String(signal || "HOLD").toUpperCase();
    if (normalized === "BUY") {
        return "tag-buy";
    }
    if (normalized === "SELL") {
        return "tag-sell";
    }
    return "tag-hold";
}

function ensureMiniCoinChart(coin) {
    const normalized = ensureCoinBucket(coin);
    if (!normalized || typeof LightweightCharts === "undefined") {
        return null;
    }

    const container = document.getElementById(`coin-mini-${coinDomId(normalized)}`);
    if (!container) {
        return null;
    }

    const existing = state.charts.miniByCoin[normalized];
    if (existing && existing.container === container) {
        return existing;
    }

    if (existing) {
        try {
            existing.resizeObserver.disconnect();
            existing.chart.remove();
        } catch (error) {
            void error;
        }
    }

    const chart = LightweightCharts.createChart(container, {
        layout: {
            background: { color: "transparent" },
            textColor: "rgba(220,245,255,0.65)",
        },
        grid: {
            vertLines: { visible: false },
            horzLines: { visible: false },
        },
        rightPriceScale: {
            visible: false,
        },
        leftPriceScale: {
            visible: false,
        },
        timeScale: {
            visible: false,
            borderVisible: false,
        },
        crosshair: {
            vertLine: { visible: false },
            horzLine: { visible: false },
        },
        width: Math.max(container.clientWidth, 120),
        height: Math.max(container.clientHeight, 92),
        handleScale: false,
        handleScroll: false,
    });

    const series = chart.addAreaSeries({
        lineColor: "#32b8ff",
        topColor: "rgba(50, 184, 255, 0.24)",
        bottomColor: "rgba(50, 184, 255, 0.03)",
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
    });

    const resizeObserver = new ResizeObserver((entries) => {
        const rect = entries[0] && entries[0].contentRect;
        if (!rect) {
            return;
        }
        chart.applyOptions({
            width: Math.max(Math.floor(rect.width), 120),
            height: Math.max(Math.floor(rect.height), 92),
        });
    });
    resizeObserver.observe(container);

    const refs = {
        chart,
        series,
        resizeObserver,
        container,
    };
    state.charts.miniByCoin[normalized] = refs;
    return refs;
}

function pruneMiniCoinCharts(validCoins) {
    const keep = new Set(
        (Array.isArray(validCoins) ? validCoins : [])
            .map((coin) => normalizeCoinSymbol(coin))
            .filter(Boolean),
    );

    Object.keys(state.charts.miniByCoin).forEach((coin) => {
        if (keep.has(coin)) {
            return;
        }

        const refs = state.charts.miniByCoin[coin];
        if (!refs) {
            delete state.charts.miniByCoin[coin];
            return;
        }

        try {
            refs.resizeObserver.disconnect();
            refs.chart.remove();
        } catch (error) {
            void error;
        }
        delete state.charts.miniByCoin[coin];
    });
}

function renderAllCoinsDashboard(symbolRows, activeSignals = []) {
    const container = document.getElementById("all-coins-grid");
    if (!container) {
        return;
    }

    const rows = Array.isArray(symbolRows) ? symbolRows : [];
    if (!rows.length) {
        container.innerHTML = '<div class="coin-card-empty">Waiting for first live snapshot...</div>';
        pruneMiniCoinCharts([]);
        return;
    }

    const signalBySymbol = new Map();
    if (Array.isArray(activeSignals)) {
        activeSignals.forEach((row) => {
            const symbol = String(row.symbol || "").toUpperCase();
            if (symbol) {
                signalBySymbol.set(symbol, row);
            }
        });
    }

    const parsedRows = rows
        .map((row) => {
            const symbol = String(row.symbol || "").toUpperCase();
            const coin = normalizeCoinSymbol(row.coin || symbol);
            return {
                symbol: symbol || `${coin}USDT`,
                coin,
                price: Number(row.price),
                ts: row.ts,
            };
        })
        .filter((row) => row.coin);

    if (!parsedRows.length) {
        container.innerHTML = '<div class="coin-card-empty">No valid coin rows in snapshot.</div>';
        pruneMiniCoinCharts([]);
        return;
    }

    parsedRows.sort((a, b) => {
        if (a.coin === state.coin && b.coin !== state.coin) {
            return -1;
        }
        if (b.coin === state.coin && a.coin !== state.coin) {
            return 1;
        }
        return a.coin.localeCompare(b.coin);
    });

    container.innerHTML = parsedRows.map((row) => {
        const signalRow = signalBySymbol.get(row.symbol) || signalBySymbol.get(`${row.coin}USDT`);
        const signal = signalRow ? String(signalRow.signal || "HOLD").toUpperCase() : "HOLD";
        const updatedAt = row.ts ? new Date(row.ts).toLocaleTimeString() : "live";
        const coinId = coinDomId(row.coin);
        const isActive = row.coin === state.coin ? " active" : "";

        return `
            <article class="coin-live-card${isActive}" onclick="selectCoinFromCard('${row.coin}')">
                <div class="coin-live-head">
                    <span class="coin-live-symbol">${row.coin} / USDT</span>
                    <span class="coin-live-price">${toPrice(row.price)}</span>
                </div>
                <div class="coin-live-meta">
                    <span class="tag ${signalTagClass(signal)}">${signal}</span>
                    <span>${updatedAt}</span>
                </div>
                <div class="coin-mini-chart" id="coin-mini-${coinId}"></div>
            </article>
        `;
    }).join("");

    const renderedCoins = [];
    parsedRows.forEach((row) => {
        renderedCoins.push(row.coin);
        const mini = ensureMiniCoinChart(row.coin);
        if (!mini) {
            return;
        }

        mini.series.applyOptions({
            lineColor: row.coin === state.coin ? "#00d1b2" : "#32b8ff",
            topColor: row.coin === state.coin ? "rgba(0, 209, 178, 0.24)" : "rgba(50, 184, 255, 0.24)",
            bottomColor: row.coin === state.coin ? "rgba(0, 209, 178, 0.03)" : "rgba(50, 184, 255, 0.03)",
        });

        const points = Array.isArray(state.liveTicksByCoin[row.coin])
            ? state.liveTicksByCoin[row.coin].slice(-120)
            : [];

        if (points.length) {
            mini.series.setData(points);
            return;
        }

        if (Number.isFinite(row.price)) {
            const now = Math.floor(Date.now() / 1000);
            mini.series.setData([
                { time: now - 1, value: row.price },
                { time: now, value: row.price },
            ]);
            return;
        }

        mini.series.setData([]);
    });

    pruneMiniCoinCharts(renderedCoins);
}

function pushLiveTick(coin, price, ts) {
    const safeCoin = ensureCoinBucket(coin);
    if (!safeCoin) {
        return;
    }

    const parsedPrice = Number(price);
    const parsedTsMs = ts ? new Date(ts).getTime() : Date.now();
    const parsedTime = Math.floor(parsedTsMs / 1000);
    if (!Number.isFinite(parsedPrice) || !Number.isFinite(parsedTime)) {
        return;
    }

    const bucket = state.liveTicksByCoin[safeCoin];
    const last = bucket[bucket.length - 1];
    if (last && last.time === parsedTime) {
        last.value = parsedPrice;
    } else {
        bucket.push({ time: parsedTime, value: parsedPrice });
    }

    if (bucket.length > 300) {
        bucket.splice(0, bucket.length - 300);
    }

    state.liveTicksByCoin[safeCoin] = bucket;
}

function applyLiveSnapshot(snapshot) {
    if (!snapshot || snapshot.type !== "market_snapshot") {
        return;
    }

    const symbols = Array.isArray(snapshot.symbols) ? snapshot.symbols : [];
    state.latestSymbols = symbols;
    renderTickerTape(symbols);

    const snapshotCoins = [];

    symbols.forEach((item) => {
        const coin = normalizeCoinSymbol(item.coin || item.symbol);
        const symbol = String(item.symbol || "").toUpperCase();
        const tickTs = item.ts || snapshot.ts;
        if (coin) {
            snapshotCoins.push(coin);
            pushLiveTick(coin, item.price, tickTs);
        } else if (symbol.endsWith("USDT")) {
            const fallbackCoin = normalizeCoinSymbol(symbol);
            if (fallbackCoin) {
                snapshotCoins.push(fallbackCoin);
                pushLiveTick(fallbackCoin, item.price, tickTs);
            }
        }
    });

    if (snapshotCoins.length) {
        setAvailableCoins(snapshotCoins);
    }

    renderAllCoinsDashboard(symbols, snapshot.active_signals || []);

    const current = symbols.find((item) => normalizeCoinSymbol(item.coin || item.symbol) === state.coin);
    const metricPrice = document.getElementById("metric-price");
    if (current && metricPrice) {
        metricPrice.textContent = toPrice(current.price);
        pulseElement(metricPrice);
    }

    if (Array.isArray(snapshot.active_signals)) {
        renderRecentSignals(snapshot.active_signals);
        const metricSignals = document.getElementById("metric-signals");
        if (metricSignals) {
            metricSignals.textContent = String(snapshot.active_signals.length);
            pulseElement(metricSignals);
        }
    }

    if (state.charts.liveSeries) {
        const line = state.liveTicksByCoin[state.coin] || [];
        if (line.length) {
            state.charts.liveSeries.setData(line);
        }
    }
}

function scheduleWsReconnect() {
    if (state.ws.retryTimer) {
        return;
    }

    const delay = state.ws.reconnectDelayMs;
    state.ws.retryTimer = setTimeout(() => {
        state.ws.retryTimer = null;
        connectLiveFeed();
    }, delay);

    state.ws.reconnectDelayMs = Math.min(
        Math.floor(state.ws.reconnectDelayMs * 1.6),
        state.ws.maxReconnectDelayMs,
    );
}

function connectLiveFeed() {
    const existing = state.ws.socket;
    if (existing && (existing.readyState === WebSocket.OPEN || existing.readyState === WebSocket.CONNECTING)) {
        return;
    }

    const wsUrl = buildWebSocketUrl("/ws/live-data");
    updateWsBadge(false, "WS CONNECTING");

    let socket;
    try {
        socket = new WebSocket(wsUrl);
    } catch (error) {
        console.warn("[BINFIN UI] websocket init failed", error);
        updateWsBadge(false, "WS RETRY");
        scheduleWsReconnect();
        return;
    }

    state.ws.socket = socket;

    socket.onopen = () => {
        state.ws.connected = true;
        state.ws.reconnectDelayMs = 1500;
        updateWsBadge(true, "WS LIVE");

        if (state.ws.retryTimer) {
            clearTimeout(state.ws.retryTimer);
            state.ws.retryTimer = null;
        }
    };

    socket.onmessage = (event) => {
        try {
            const payload = JSON.parse(event.data);
            applyLiveSnapshot(payload);
        } catch (error) {
            console.warn("[BINFIN UI] websocket payload parse failed", error);
        }
    };

    socket.onerror = () => {
        updateWsBadge(false, "WS ERROR");
    };

    socket.onclose = () => {
        state.ws.connected = false;
        updateWsBadge(false, "WS RECONNECT");
        scheduleWsReconnect();
    };
}

async function loadSignalHistory() {
    const signals = await safeFetch("/signals/?page_size=150");
    renderSignalHistory(signals);
}

async function loadRedditFeed() {
    const redditFeed = await safeFetch(`/live/reddit?coin=${state.coin}&limit=20&minutes=360`);
    renderRedditFeed(redditFeed);
}

async function triggerModelTraining() {
    const output = document.getElementById("training-output");
    const timestamp = new Date().toLocaleTimeString();
    const payload = {
        symbols: [`${state.coin}USDT`],
        interval: state.interval,
        max_rows_per_symbol: 6000,
        sentiment_sample_size: 30,
    };

    if (output) {
        output.innerHTML += `<br>> [${timestamp}] Launching model training for ${state.coin}...`;
    }

    const result = await safeFetch("/model/train-finance-news", {
        method: "POST",
        body: JSON.stringify(payload),
    });

    if (output) {
        if (result && Array.isArray(result.results)) {
            output.innerHTML += `<br>> [${new Date().toLocaleTimeString()}] Training complete for ${result.results.length} symbol(s).`;
        } else {
            output.innerHTML += `<br>> [${new Date().toLocaleTimeString()}] Training request failed or returned no result.`;
        }
    }
}

async function refreshDashboard() {
    const [health, activeSignals, outcomes, performance, coinDetails, runtime, predictions, redditFeed] = await Promise.all([
        safeFetch("/health/"),
        safeFetch("/signals/active?limit=20"),
        safeFetch("/signals/recent-outcomes"),
        safeFetch("/signals/performance?group_by=coin"),
        safeFetch(`/coins/${state.coin}`),
        safeFetch("/model/runtime"),
        safeFetch(`/predictions/?symbol=${state.coin}USDT&hours=72&limit=300`),
        safeFetch(`/live/reddit?coin=${state.coin}&limit=20&minutes=360`),
    ]);

    setStatus(health);

    let effectiveCoinDetails = coinDetails;
    if (!effectiveCoinDetails && Array.isArray(state.latestSymbols) && state.latestSymbols.length) {
        const liveRow = state.latestSymbols.find((item) => normalizeCoinSymbol(item.coin || item.symbol) === state.coin);
        if (liveRow) {
            effectiveCoinDetails = {
                current_price: Number(liveRow.price),
                change_24h_pct: null,
            };
        }
    }

    updateOverview(activeSignals, outcomes, effectiveCoinDetails, runtime);
    renderRecentSignals(activeSignals);
    updateRuntimePanel(runtime);
    renderRedditFeed(redditFeed);

    const apiOrigin = document.getElementById("api-origin");
    if (apiOrigin) {
        apiOrigin.textContent = window.location.origin;
    }

    setLastRefresh();

    const activeView = document.querySelector(".view-section.active");
    if (activeView && activeView.id === "view-charts") {
        await initPriceChart();
        await initConfidenceChart(predictions);
    }

    if (activeView && activeView.id === "view-signals") {
        const historyRows = await safeFetch("/signals/?page_size=150");
        renderSignalHistory(historyRows);
    }

    if (performance && Array.isArray(performance.rows)) {
        const performanceCoins = performance.rows
            .map((row) => normalizeCoinSymbol(row.coin || row.symbol))
            .filter(Boolean);
        if (performanceCoins.length) {
            setAvailableCoins(performanceCoins);
        }
    }

    if (Array.isArray(state.latestSymbols) && state.latestSymbols.length) {
        renderAllCoinsDashboard(state.latestSymbols, Array.isArray(activeSignals) ? activeSignals : []);
    }
}

function manualRefresh() {
    refreshDashboard();
}

function restartRefreshTimer() {
    if (state.refreshTimer) {
        clearInterval(state.refreshTimer);
    }

    state.refreshTimer = setInterval(() => {
        refreshDashboard();
    }, state.refreshSeconds * 1000);
}

async function bootstrapApp() {
    const coinSelector = document.getElementById("global-coin-select");
    const intervalSelector = document.getElementById("global-interval-select");
    const refreshSlider = document.getElementById("refresh-seconds");

    if (coinSelector) {
        state.coin = normalizeCoinSymbol(coinSelector.value) || state.coin;
    }
    if (intervalSelector) {
        state.interval = intervalSelector.value;
    }
    if (refreshSlider) {
        state.refreshSeconds = Number.parseInt(refreshSlider.value, 10) || state.refreshSeconds;
        updateRefreshLabel(refreshSlider.value);
    }

    await loadCoinUniverse();
    connectLiveFeed();
    restartRefreshTimer();
    await refreshDashboard();
}

window.addEventListener("load", () => {
    bootstrapApp();
});
window.addEventListener("beforeunload", () => {
    if (state.ws.retryTimer) {
        clearTimeout(state.ws.retryTimer);
    }
    if (state.refreshTimer) {
        clearInterval(state.refreshTimer);
    }
    if (state.ws.socket && state.ws.socket.readyState === WebSocket.OPEN) {
        state.ws.socket.close();
    }
    pruneMiniCoinCharts([]);
});
