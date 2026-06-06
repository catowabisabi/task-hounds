import { useEffect, useRef, useState } from "react";
import { apiGet } from "../lib/api";

export function useStream(agentName: string) {
  const [content, setContent] = useState("");
  const lastRef = useRef("");

  useEffect(() => {
    let stopped = false;
    const load = () => {
      apiGet<{ content: string }>(`/api/stream/${agentName}`)
        .then(d => {
          if (stopped) return;
          const next = d.content ?? "";
          if (next !== lastRef.current) {
            lastRef.current = next;
            setContent(next);
          }
        })
        .catch(() => {});
    };
    load();
    const id = setInterval(load, 1000);
    return () => {
      stopped = true;
      clearInterval(id);
    };
  }, [agentName]);

  return content;
}
