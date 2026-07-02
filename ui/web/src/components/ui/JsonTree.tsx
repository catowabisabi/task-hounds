import { useState } from "react";

interface JsonTreeProps {
  data: unknown;
  depth?: number;
  maxDepth?: number;
}

export function JsonTree({ data, depth = 0, maxDepth = 6 }: JsonTreeProps) {
  const [collapsed, setCollapsed] = useState(depth > 2);

  if (data === null) return <span style={{ color: "var(--amber)" }}>null</span>;
  if (data === undefined) return <span style={{ color: "var(--text-dim)" }}>undefined</span>;
  if (typeof data === "boolean") return <span style={{ color: "var(--purple)" }}>{String(data)}</span>;
  if (typeof data === "number") return <span style={{ color: "var(--blue)" }}>{data}</span>;
  if (typeof data === "string") return <span style={{ color: "var(--green)" }}>"{data}"</span>;

  if (Array.isArray(data)) {
    if (data.length === 0) return <span style={{ color: "var(--text-dim)" }}>[]</span>;
    return (
      <div style={{ marginLeft: depth > 0 ? 16 : 0 }}>
        <span
          onClick={() => setCollapsed(!collapsed)}
          style={{ cursor: "pointer", color: "var(--text-dim)" }}
        >
          {collapsed ? "▶ [" : "▼ ["}{!collapsed && ` ${data.length} items`}
        </span>
        {!collapsed && data.map((item, i) => (
          <div key={i} style={{ marginLeft: 16 }}>
            <JsonTree data={item} depth={depth + 1} maxDepth={maxDepth} />
          </div>
        ))}
        {collapsed && <span style={{ color: "var(--text-dim)" }}>]</span>}
      </div>
    );
  }

  if (typeof data === "object") {
    const entries = Object.entries(data as Record<string, unknown>);
    if (entries.length === 0) return <span style={{ color: "var(--text-dim)" }}>{"{}"}</span>;
    return (
      <div style={{ marginLeft: depth > 0 ? 16 : 0 }}>
        <span
          onClick={() => setCollapsed(!collapsed)}
          style={{ cursor: "pointer", color: "var(--text-dim)" }}
        >
          {collapsed ? `▶ { ${entries.length} keys }` : "▼ {"}
        </span>
        {!collapsed && entries.map(([key, value]) => (
          <div key={key} style={{ marginLeft: 16 }}>
            <span style={{ color: "var(--text-secondary)" }}>{key}: </span>
            <JsonTree data={value} depth={depth + 1} maxDepth={maxDepth} />
          </div>
        ))}
        {collapsed && <span style={{ color: "var(--text-dim)" }}>{"}"}</span>}
      </div>
    );
  }

  return <span>{String(data)}</span>;
}