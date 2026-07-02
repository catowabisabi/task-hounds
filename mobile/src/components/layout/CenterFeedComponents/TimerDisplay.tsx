import { useState, useEffect } from "react";
import { apiGet } from "../../../lib/api";

export function TimerDisplay({ agentName }: { agentName: string }) {
  const [text, setText] = useState("");
  useEffect(() => {
    const load = () =>
      apiGet<{ content: string }>(`/api/timer/${agentName}`)
        .then(d => setText(d.content))
        .catch(() => {});
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [agentName]);
  if (!text || text === "0m 0s") return null;
  return <span className="text-[11px]" style={{ color: "#60a5fa" }}>next: {text}</span>;
}