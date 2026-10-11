import {
  CustomerSegment,
  InventoryForecast,
  OmniApiClient,
  RetailOverview,
  RetailProduct,
  ShopAssistResponse,
  StockRisk,
} from "@omni/contracts";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

const CATEGORY_GLYPHS: Record<string, string> = {
  "Rain & Weather": "☔",
  "Outdoor & Travel": "⛺",
  "Coffee & Tea": "☕",
  "Home & Kitchen": "🍳",
  "Beauty & Wellness": "🧴",
  "Tech Accessories": "🔌",
  "Festive & Gifts": "🎁",
};

const RISK_LABELS: Record<StockRisk, string> = {
  critical: "▲ Stock-out before restock",
  high: "● Reorder now",
  watch: "◆ Watch",
  ok: "✓ Healthy",
};

const STARTERS = [
  "Rain gear for my daily commute under $50",
  "A Deepavali gift for my in-laws",
  "I'm getting into pour-over coffee",
  "Camping on Pulau Ubin this weekend",
];

export function formatMoney(cents: number, currency = "SGD") {
  const amount = new Intl.NumberFormat("en-SG", {
    minimumFractionDigits: cents >= 1_000_000 ? 0 : 2,
    maximumFractionDigits: cents >= 1_000_000 ? 0 : 2,
  }).format(cents / 100);
  return currency === "SGD" ? `S$${amount}` : `${currency} ${amount}`;
}

function formatPercent(value: number) {
  return `${Math.round(value * 100)}%`;
}

function shortDate(value: string) {
  return new Date(`${value}T00:00:00`).toLocaleDateString("en-SG", {
    day: "numeric",
    month: "short",
  });
}

function backendLabel(value?: string) {
  if (value === "vertex_ai_search") return "Vertex AI Search";
  if (value === "bigquery") return "BigQuery AI.FORECAST";
  if (value === "local_fallback") return "Local ranking (fallback)";
  return "Local engine";
}

function ProductTile({
  product,
  compact = false,
}: {
  product: RetailProduct;
  compact?: boolean;
}) {
  const stock = !product.in_stock
    ? "Out of stock"
    : product.on_hand < 10
      ? `Only ${product.on_hand} left`
      : "In stock";
  return (
    <article className={`product-tile ${compact ? "compact" : ""}`}>
      <div className="product-glyph" aria-hidden="true">
        {CATEGORY_GLYPHS[product.category] ?? "🛍"}
      </div>
      <div className="product-body">
        <small>
          {product.brand} · {product.category}
        </small>
        <strong>{product.name}</strong>
        <div className="product-meta">
          <span className="product-price">
            {formatMoney(product.price_cents, product.currency)}
          </span>
          <span
            className={`stock-note ${!product.in_stock ? "out" : product.on_hand < 10 ? "low" : ""}`}
          >
            {stock}
          </span>
        </div>
        {product.reason && <p className="product-reason">{product.reason}</p>}
      </div>
    </article>
  );
}

type ShopMessage =
  | { role: "user"; content: string }
  | { role: "assistant"; content: string; result: ShopAssistResponse };

export function StorefrontPage({
  api,
  tenantId,
}: {
  api: OmniApiClient;
  tenantId: string;
}) {
  const [shopperId, setShopperId] = useState("");
  const [messages, setMessages] = useState<ShopMessage[]>([]);
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement | null>(null);
  const shoppers = useQuery({
    queryKey: ["shoppers", tenantId],
    queryFn: () => api.shoppers(),
  });
  const picks = useQuery({
    queryKey: ["shop-picks", tenantId, shopperId],
    queryFn: () => api.shopRecommendations(shopperId || null),
  });
  const ask = useMutation({
    mutationFn: (message: string) =>
      api.shopAssist({
        message,
        customer_id: shopperId || null,
        history: messages
          .slice(-6)
          .map(({ role, content }) => ({ role, content })),
      }),
    onSuccess: (result) =>
      setMessages((current) => [
        ...current,
        { role: "assistant", content: result.reply, result },
      ]),
  });
  const shopper = shoppers.data?.find((item) => item.id === shopperId);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [messages, ask.isPending]);

  const send = (message: string) => {
    const text = message.trim();
    if (!text || ask.isPending) return;
    setMessages((current) => [...current, { role: "user", content: text }]);
    setDraft("");
    ask.mutate(text);
  };

  return (
    <main className="content storefront">
      <div className="page-heading">
        <div>
          <span className="eyebrow">Storefront</span>
          <h1>Shop with Harbourline AI</h1>
          <p>
            Conversational shopping grounded in the live catalogue, stock and
            each shopper's history.
          </p>
        </div>
        <label className="shopper-picker">
          Shopping as
          <select
            aria-label="Shopping as"
            value={shopperId}
            onChange={(event) => {
              setShopperId(event.target.value);
              setMessages([]);
            }}
          >
            <option value="">Guest shopper</option>
            {shoppers.data?.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name} · {item.segment}
              </option>
            ))}
          </select>
        </label>
      </div>

      <section className="retail-section">
        <div className="section-title">
          <h2>{shopper ? `Picked for ${shopper.name}` : "Trending now"}</h2>
          <span>
            {shopper
              ? `Based on ${shopper.orders} orders and what similar shoppers buy`
              : "Fastest-moving products this fortnight"}
          </span>
        </div>
        <div className="product-row">
          {picks.data?.map((product) => (
            <ProductTile key={product.id} product={product} compact />
          ))}
          {picks.isPending && <p className="muted">Loading picks…</p>}
        </div>
      </section>

      <section className="shop-chat" aria-label="Shopping assistant">
        <div className="shop-thread">
          {!messages.length && (
            <div className="shop-empty">
              <strong>What are you shopping for today?</strong>
              <p>Describe the need, occasion or budget in your own words.</p>
              <div className="chip-row">
                {STARTERS.map((starter) => (
                  <button
                    key={starter}
                    className="chip"
                    onClick={() => send(starter)}
                  >
                    {starter}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((message, index) =>
            message.role === "user" ? (
              <div className="shop-bubble user" key={index}>
                {message.content}
              </div>
            ) : (
              <div className="shop-bubble assistant" key={index}>
                <p>{message.content}</p>
                {message.result.products.length > 0 && (
                  <div className="product-grid">
                    {message.result.products.slice(0, 4).map((product) => (
                      <ProductTile key={product.id} product={product} />
                    ))}
                  </div>
                )}
                {message.result.bundle.length > 0 && (
                  <div className="bundle">
                    <span className="eyebrow">
                      Frequently bought with {message.result.bundle_anchor}
                    </span>
                    <div className="bundle-items">
                      {message.result.bundle.map((item) => (
                        <span key={item.id} className="bundle-item">
                          {CATEGORY_GLYPHS[item.category] ?? "🛍"} {item.name}
                          <em>
                            {formatMoney(item.price_cents, item.currency)} ·{" "}
                            {formatPercent(item.confidence)} of baskets
                          </em>
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                <div className="chip-row">
                  {message.result.follow_up_questions.map((question) => (
                    <button
                      key={question}
                      className="chip"
                      onClick={() => send(question)}
                    >
                      {question}
                    </button>
                  ))}
                </div>
                <small className="shop-provenance">
                  {backendLabel(message.result.search_backend)} ·{" "}
                  {message.result.model}
                  {message.result.personalized ? " · personalised" : ""}
                  {message.result.degraded ? " · AI fallback mode" : ""}
                </small>
              </div>
            ),
          )}
          {ask.isPending && (
            <div className="shop-bubble assistant typing" aria-live="polite">
              Finding the best matches…
            </div>
          )}
          {ask.error && <div className="error-banner">{ask.error.message}</div>}
          <div ref={endRef} />
        </div>
        <form
          className="shop-input"
          onSubmit={(event) => {
            event.preventDefault();
            send(draft);
          }}
        >
          <input
            aria-label="Message the shopping assistant"
            placeholder="e.g. something to stop my wardrobe smelling musty, under $150"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            maxLength={1000}
          />
          <button disabled={ask.isPending || !draft.trim()}>Send</button>
        </form>
      </section>
    </main>
  );
}

function Sparkline({ values, label }: { values: number[]; label: string }) {
  const width = 112;
  const height = 28;
  const max = Math.max(...values, 1);
  const points = values
    .map((value, index) => {
      const x = (index / Math.max(1, values.length - 1)) * (width - 4) + 2;
      const y = height - 3 - (value / max) * (height - 6);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg
      className="sparkline"
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      role="img"
      aria-label={label}
    >
      <title>
        {label}: {values.map((value) => value.toFixed(1)).join(", ")}
      </title>
      <polyline points={points} />
    </svg>
  );
}

function RevenueChart({ overview }: { overview: RetailOverview }) {
  const [hovered, setHovered] = useState<number | null>(null);
  const days = overview.daily;
  const width = 720;
  const height = 170;
  const max = Math.max(...days.map((day) => day.revenue_cents), 1);
  const slot = width / days.length;
  const active = hovered === null ? null : days[hovered];
  return (
    <figure className="revenue-chart">
      <figcaption>
        <span>Daily revenue · last 60 days</span>
        <strong>
          {active
            ? `${shortDate(active.date)} · ${formatMoney(active.revenue_cents, overview.currency)} · ${active.orders} orders`
            : `Peak ${formatMoney(max, overview.currency)}`}
        </strong>
      </figcaption>
      <svg
        viewBox={`0 0 ${width} ${height + 18}`}
        role="img"
        aria-label="Daily revenue bar chart"
        onMouseLeave={() => setHovered(null)}
      >
        <line
          className="chart-baseline"
          x1="0"
          x2={width}
          y1={height}
          y2={height}
        />
        {days.map((day, index) => {
          const barHeight = Math.max(
            2,
            (day.revenue_cents / max) * (height - 8),
          );
          return (
            <g key={day.date} onMouseEnter={() => setHovered(index)}>
              <rect
                className="chart-hit"
                x={index * slot}
                y={0}
                width={slot}
                height={height}
              />
              <rect
                className={`chart-bar ${hovered === index ? "active" : ""}`}
                x={index * slot + 1}
                y={height - barHeight}
                width={Math.max(1, slot - 2)}
                height={barHeight}
                rx="2"
              />
            </g>
          );
        })}
        <text x="0" y={height + 14} className="chart-label">
          {shortDate(days[0]?.date ?? overview.as_of)}
        </text>
        <text
          x={width}
          y={height + 14}
          className="chart-label"
          textAnchor="end"
        >
          {shortDate(days[days.length - 1]?.date ?? overview.as_of)}
        </text>
      </svg>
      <table className="sr-only">
        <caption>Daily revenue</caption>
        <tbody>
          {days.map((day) => (
            <tr key={day.date}>
              <th>{day.date}</th>
              <td>{formatMoney(day.revenue_cents, overview.currency)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}

function campaignPrompt(segment: CustomerSegment) {
  return `Draft a campaign for ${segment.segment} customers. They mostly buy ${segment.top_categories.join(", ")}. Goal: ${segment.recommended_action}`;
}

export function MerchantInsightsPage({
  api,
  tenantId,
  onAskAgent,
}: {
  api: OmniApiClient;
  tenantId: string;
  onAskAgent: (prompt: string) => void;
}) {
  const [horizon, setHorizon] = useState(14);
  const config = useQuery({
    queryKey: ["retail-config", tenantId],
    queryFn: () => api.retailConfig(),
  });
  const overview = useQuery({
    queryKey: ["retail-overview", tenantId],
    queryFn: () => api.retailOverview(),
  });
  const inventory = useQuery({
    queryKey: ["retail-inventory", tenantId, horizon],
    queryFn: () => api.inventoryForecast(horizon),
  });
  const segments = useQuery({
    queryKey: ["retail-segments", tenantId],
    queryFn: () => api.customerSegments(),
  });
  const basket = useQuery({
    queryKey: ["retail-basket", tenantId],
    queryFn: () => api.basketRules(),
  });
  const purchaseOrders = useQuery({
    queryKey: ["purchase-orders", tenantId],
    queryFn: () => api.purchaseOrders(),
  });
  const campaigns = useQuery({
    queryKey: ["retail-campaigns", tenantId],
    queryFn: () => api.retailCampaigns(),
  });
  const currency = overview.data?.currency ?? "SGD";
  const atRisk = (inventory.data ?? []).filter((row) => row.risk !== "ok");
  const forecastSource = inventory.data?.[0]?.source;

  if (overview.isError)
    return (
      <main className="content">
        <div className="error-banner">{overview.error.message}</div>
      </main>
    );
  return (
    <main className="content insights">
      <div className="page-heading">
        <div>
          <span className="eyebrow">Merchant intelligence</span>
          <h1>Harbourline at a glance</h1>
          <p>
            Live demand, stock risk and customer signals
            {overview.data ? ` as of ${overview.data.as_of}` : ""}.
          </p>
        </div>
        <div className="provenance-badges">
          <span>
            Forecast:{" "}
            {forecastSource === "bigquery_ai_forecast"
              ? "BigQuery AI.FORECAST"
              : "Seasonal model"}
          </span>
          <span>Search: {backendLabel(config.data?.search_backend)}</span>
          <span>AI: {config.data?.ai_model ?? "…"}</span>
        </div>
      </div>

      {overview.data && (
        <>
          <div className="kpi-row">
            <div className="kpi">
              <span>Revenue · 30 days</span>
              <strong>
                {formatMoney(overview.data.revenue_30d_cents, currency)}
              </strong>
              {overview.data.revenue_change_pct !== null && (
                <em
                  className={
                    overview.data.revenue_change_pct >= 0 ? "up" : "down"
                  }
                >
                  {overview.data.revenue_change_pct >= 0 ? "▲" : "▼"}{" "}
                  {Math.abs(overview.data.revenue_change_pct)}% vs prior 30 days
                </em>
              )}
            </div>
            <div className="kpi">
              <span>Orders · 30 days</span>
              <strong>{overview.data.orders_30d.toLocaleString()}</strong>
              <em>{overview.data.active_customers_30d} active members</em>
            </div>
            <div className="kpi">
              <span>Average order</span>
              <strong>
                {formatMoney(overview.data.average_order_value_cents, currency)}
              </strong>
            </div>
            <div className="kpi">
              <span>Repeat customers · 90 days</span>
              <strong>{formatPercent(overview.data.repeat_rate_90d)}</strong>
            </div>
            <div className="kpi">
              <span>Online share</span>
              <strong>{formatPercent(overview.data.online_share)}</strong>
            </div>
          </div>
          <RevenueChart overview={overview.data} />
        </>
      )}

      <section className="retail-section">
        <div className="section-title">
          <div>
            <h2>Stock-out risk</h2>
            <span>
              Forecast demand over supplier lead time plus a {horizon}-day
              horizon
            </span>
          </div>
          <div className="row-actions">
            <select
              aria-label="Forecast horizon"
              value={horizon}
              onChange={(event) => setHorizon(Number(event.target.value))}
            >
              {[7, 14, 30].map((days) => (
                <option key={days} value={days}>
                  {days} days
                </option>
              ))}
            </select>
            <button
              onClick={() =>
                onAskAgent(
                  `Which products will stock out in the next ${horizon} days? Draft restock orders for them.`,
                )
              }
            >
              Draft restock with Omni Agent
            </button>
          </div>
        </div>
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Product</th>
                <th>Status</th>
                <th>On hand</th>
                <th>Days of cover</th>
                <th>Stock-out</th>
                <th>Forecast · {horizon}d</th>
                <th>Suggested reorder</th>
              </tr>
            </thead>
            <tbody>
              {atRisk.map((row: InventoryForecast) => (
                <tr key={row.product_id}>
                  <td>
                    <strong>{row.name}</strong>
                    <small>
                      {row.sku} · {row.supplier}
                    </small>
                  </td>
                  <td>
                    <span className={`risk-pill ${row.risk}`}>
                      {RISK_LABELS[row.risk]}
                    </span>
                  </td>
                  <td>
                    {row.on_hand}
                    {row.on_order > 0 && (
                      <small>+{row.on_order} on order</small>
                    )}
                  </td>
                  <td>{row.days_of_cover ?? "—"}</td>
                  <td>
                    {row.stockout_date ? shortDate(row.stockout_date) : "—"}
                    <small>{row.lead_time_days}-day lead time</small>
                  </td>
                  <td>
                    <Sparkline
                      values={row.forecast_daily}
                      label={`${row.name} daily forecast`}
                    />
                    <small>
                      {row.forecast_units} units ·{" "}
                      {row.trend_pct >= 0 ? "+" : ""}
                      {row.trend_pct}% trend
                    </small>
                  </td>
                  <td>
                    <strong>{row.recommended_quantity}</strong>
                    <small>
                      {formatMoney(row.recommended_cost_cents, currency)}
                    </small>
                  </td>
                </tr>
              ))}
              {!atRisk.length && inventory.isSuccess && (
                <tr>
                  <td colSpan={7}>Every product is covered.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="retail-section">
        <div className="section-title">
          <div>
            <h2>Customer segments</h2>
            <span>RFM segmentation over 180 days of orders</span>
          </div>
        </div>
        <div className="segment-grid">
          {segments.data?.map((segment) => (
            <article className="segment-card" key={segment.segment}>
              <div className="segment-head">
                <h3>{segment.segment}</h3>
                <strong>{segment.customers}</strong>
              </div>
              <div
                className="share-bar"
                role="img"
                aria-label={`${formatPercent(segment.share)} of customers`}
              >
                <span
                  style={{ width: `${Math.max(2, segment.share * 100)}%` }}
                />
              </div>
              <p>{segment.description}</p>
              <dl>
                <div>
                  <dt>Revenue</dt>
                  <dd>{formatMoney(segment.revenue_cents, currency)}</dd>
                </div>
                <div>
                  <dt>Avg order</dt>
                  <dd>
                    {formatMoney(segment.avg_order_value_cents, currency)}
                  </dd>
                </div>
                <div>
                  <dt>Loves</dt>
                  <dd>
                    {segment.top_categories.slice(0, 2).join(", ") || "—"}
                  </dd>
                </div>
              </dl>
              <button
                className="secondary"
                disabled={!segment.customers}
                onClick={() => onAskAgent(campaignPrompt(segment))}
              >
                Draft campaign
              </button>
            </article>
          ))}
        </div>
      </section>

      <div className="split-sections">
        <section className="retail-section">
          <div className="section-title">
            <div>
              <h2>Bought together</h2>
              <span>Association rules ranked by lift</span>
            </div>
          </div>
          <ol className="pair-list">
            {basket.data?.slice(0, 8).map((pair) => (
              <li key={`${pair.anchor_id}-${pair.companion_id}`}>
                <span>
                  {pair.anchor_name} <b>+</b> {pair.companion_name}
                </span>
                <small>
                  {pair.orders_together} orders ·{" "}
                  {formatPercent(pair.confidence)} confidence · lift {pair.lift}
                </small>
              </li>
            ))}
          </ol>
        </section>
        <section className="retail-section">
          <div className="section-title">
            <div>
              <h2>Approved actions</h2>
              <span>Every AI proposal passed human review</span>
            </div>
          </div>
          <ul className="action-list">
            {purchaseOrders.data?.map((order) => (
              <li key={order.id}>
                <span className="status-pill">{order.status}</span>
                <div>
                  <strong>
                    {order.number} · {order.supplier}
                  </strong>
                  <small>
                    {order.lines
                      .map((line) => `${line.quantity}× ${line.name}`)
                      .join(", ")}{" "}
                    · {formatMoney(order.total_cost_cents, order.currency)}
                  </small>
                </div>
              </li>
            ))}
            {campaigns.data?.map((campaign) => (
              <li key={campaign.id}>
                <span className="status-pill">{campaign.channel}</span>
                <div>
                  <strong>
                    {campaign.segment} · {campaign.audience_size} customers
                  </strong>
                  <small>{campaign.offer}</small>
                </div>
              </li>
            ))}
            {!purchaseOrders.data?.length && !campaigns.data?.length && (
              <li className="muted">
                No approved purchase orders or campaigns yet. Ask Omni Agent to
                draft one.
              </li>
            )}
          </ul>
        </section>
      </div>

      {config.data?.looker_embed_url && (
        <section className="retail-section">
          <div className="section-title">
            <div>
              <h2>Looker Studio dashboard</h2>
              <span>BigQuery forecasts, segments and basket rules</span>
            </div>
          </div>
          <iframe
            className="looker-frame"
            title="Looker Studio retail dashboard"
            src={config.data.looker_embed_url}
            allowFullScreen
            sandbox="allow-storage-access-by-user-activation allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox"
          />
        </section>
      )}
    </main>
  );
}

export function CatalogPage({
  api,
  tenantId,
}: {
  api: OmniApiClient;
  tenantId: string;
}) {
  const [query, setQuery] = useState("");
  const products = useQuery({
    queryKey: ["retail-products", tenantId, query],
    queryFn: () => api.retailProducts(query),
  });
  const orders = useQuery({
    queryKey: ["retail-orders", tenantId],
    queryFn: () => api.retailOrders(25),
  });
  return (
    <main className="content">
      <div className="page-heading">
        <div>
          <span className="eyebrow">Commerce</span>
          <h1>Catalogue & orders</h1>
          <p>
            {products.data?.length ?? "…"} products across online and two
            stores.
          </p>
        </div>
      </div>
      <input
        className="search-box"
        type="search"
        aria-label="Search products"
        placeholder="Search products, tags or brands"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Product</th>
              <th>Category</th>
              <th>Price</th>
              <th>On hand</th>
              <th>On order</th>
              <th>Supplier</th>
            </tr>
          </thead>
          <tbody>
            {products.data?.map((product) => (
              <tr key={product.id}>
                <td>
                  <strong>{product.name}</strong>
                  <small>
                    {product.sku} · {product.brand}
                  </small>
                </td>
                <td>{product.category}</td>
                <td>{formatMoney(product.price_cents, product.currency)}</td>
                <td>{product.on_hand}</td>
                <td>{product.on_order || "—"}</td>
                <td>
                  {product.supplier}
                  <small>{product.lead_time_days}-day lead time</small>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <section className="retail-section">
        <div className="section-title">
          <div>
            <h2>Latest orders</h2>
            <span>Online and in-store, newest first</span>
          </div>
        </div>
        <ul className="action-list">
          {orders.data?.map((order) => (
            <li key={order.id}>
              <span className="status-pill">{order.channel}</span>
              <div>
                <strong>
                  {order.number} · {order.customer_name ?? "Guest"} ·{" "}
                  {formatMoney(order.total_cents, order.currency)}
                </strong>
                <small>
                  {new Date(order.placed_at).toLocaleString("en-SG")} ·{" "}
                  {order.store} ·{" "}
                  {order.items
                    .map((item) => `${item.quantity}× ${item.name}`)
                    .join(", ")}
                </small>
              </div>
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}

export interface RestockLine {
  sku: string;
  name?: string;
  quantity: number;
  on_hand?: number;
  forecast_units?: number;
  stockout_date?: string | null;
  risk?: StockRisk;
  supplier?: string;
}

export function isRestockLines(value: unknown): value is RestockLine[] {
  return (
    Array.isArray(value) &&
    value.every(
      (item) =>
        typeof item === "object" &&
        item !== null &&
        "sku" in item &&
        "quantity" in item,
    )
  );
}

export function RestockLinesTable({
  lines,
  onChange,
}: {
  lines: RestockLine[];
  onChange?: (lines: RestockLine[]) => void;
}) {
  return (
    <div className="table-wrap restock-lines">
      <table className="data-table">
        <thead>
          <tr>
            <th>Product</th>
            <th>Risk</th>
            <th>On hand</th>
            <th>Forecast</th>
            <th>Stock-out</th>
            <th>Order qty</th>
          </tr>
        </thead>
        <tbody>
          {lines.map((line, index) => (
            <tr key={line.sku}>
              <td>
                <strong>{line.name ?? line.sku}</strong>
                <small>
                  {line.sku}
                  {line.supplier ? ` · ${line.supplier}` : ""}
                </small>
              </td>
              <td>
                {line.risk && (
                  <span className={`risk-pill ${line.risk}`}>
                    {RISK_LABELS[line.risk]}
                  </span>
                )}
              </td>
              <td>{line.on_hand ?? "—"}</td>
              <td>{line.forecast_units ?? "—"}</td>
              <td>
                {line.stockout_date ? shortDate(line.stockout_date) : "—"}
              </td>
              <td>
                {onChange ? (
                  <input
                    type="number"
                    min={0}
                    aria-label={`Order quantity for ${line.name ?? line.sku}`}
                    value={line.quantity}
                    onChange={(event) =>
                      onChange(
                        lines.map((item, itemIndex) =>
                          itemIndex === index
                            ? {
                                ...item,
                                quantity: Math.max(
                                  0,
                                  Number(event.target.value) || 0,
                                ),
                              }
                            : item,
                        ),
                      )
                    }
                  />
                ) : (
                  <strong>{line.quantity}</strong>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
