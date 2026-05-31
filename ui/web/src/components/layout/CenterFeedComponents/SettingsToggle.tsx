import { useState, useEffect, useCallback } from "react";
import { apiGet, apiPost } from "../../../lib/api";

const LANGUAGES = [
  { value: "en", label: "English" },
  { value: "zh-tw", label: "繁體中文" },
  { value: "ja", label: "日本語" },
];

export function SettingsToggle({ onRefresh }: { onRefresh: () => void }) {
  const [open, setOpen] = useState(false);
  const [language, setLanguage] = useState("en");
  const [silenceTimeout, setSilenceTimeout] = useState(480);
  const [hardTimeout, setHardTimeout] = useState(1200);
  const [opencodeBin, setOpencodeBin] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    apiGet<{ language?: string; silence_timeout?: number; silence_timeout_seconds?: number; hard_timeout?: number; hard_timeout_seconds?: number; opencode_bin?: string }>("/api/settings")
      .then(s => {
        if (s.language) setLanguage(s.language);
        setSilenceTimeout(Number(s.silence_timeout_seconds ?? s.silence_timeout ?? 480));
        setHardTimeout(Number(s.hard_timeout_seconds ?? s.hard_timeout ?? 1200));
        setOpencodeBin(s.opencode_bin ?? "");
      })
      .catch(() => {});
  }, []);

  const handleLanguageChange = useCallback(async (lang: string) => {
    setLanguage(lang);
    setSaving(true);
    try {
      await apiPost("/api/settings", { language: lang });
      onRefresh();
    } catch {
      apiGet<{ language?: string }>("/api/settings")
        .then(s => { if (s.language) setLanguage(s.language); })
        .catch(() => {});
    } finally {
      setSaving(false);
    }
  }, [onRefresh]);

  const saveTimeouts = useCallback(async () => {
    setSaving(true);
    try {
      await apiPost("/api/settings", {
        silence_timeout_seconds: silenceTimeout,
        hard_timeout_seconds: hardTimeout,
      });
      onRefresh();
    } finally {
      setSaving(false);
    }
  }, [hardTimeout, onRefresh, silenceTimeout]);

  const saveOpencodeBin = useCallback(async () => {
    setSaving(true);
    try {
      await apiPost("/api/settings", {
        opencode_bin: opencodeBin.trim(),
      });
      onRefresh();
    } finally {
      setSaving(false);
    }
  }, [onRefresh, opencodeBin]);

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        className="px-3 py-1.5 text-[11px] rounded transition-colors duration-200 btn-blue-accent"
        style={{ background: "var(--bg-panel)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}
      >
        ⚙ Settings
      </button>
      {open && (
        <div
          className="absolute right-0 top-full mt-1 w-56 rounded-lg shadow-xl z-20 p-3"
          style={{ background: "var(--bg-raised)", border: "1px solid var(--border)" }}
        >
          <p className="text-[10px] font-semibold uppercase tracking-wider mb-2" style={{ color: "var(--text-dim)" }}>Language</p>
          <div className="space-y-1">
            {LANGUAGES.map(lang => (
              <button
                key={lang.value}
                onClick={() => { handleLanguageChange(lang.value); setOpen(false); }}
                disabled={saving}
                className="w-full text-left px-2 py-1 text-[11px] rounded transition-colors disabled:opacity-40"
                style={{
                  background: language === lang.value ? "var(--amber-bg)" : "transparent",
                  color: language === lang.value ? "var(--amber)" : "var(--text-secondary)",
                  border: language === lang.value ? "1px solid var(--amber-dim)" : "1px solid transparent",
                }}
              >
                {lang.label}
                {language === lang.value && " ✓"}
              </button>
            ))}
          </div>
          <div style={{ borderTop: "1px solid var(--border)", marginTop: "8px", paddingTop: "8px" }}>
            <p className="text-[10px] font-semibold uppercase tracking-wider mb-2" style={{ color: "var(--text-dim)" }}>Timeouts</p>
            <label className="block text-[10px] mb-1" style={{ color: "var(--text-secondary)" }}>
              Silence
              <input
                type="number"
                min={30}
                step={30}
                value={silenceTimeout}
                onChange={e => setSilenceTimeout(Number(e.target.value))}
                className="w-full mt-1 px-2 py-1 rounded text-[11px]"
                style={{ background: "var(--bg-panel)", color: "var(--text-primary)", border: "1px solid var(--border)" }}
              />
            </label>
            <label className="block text-[10px] mb-2" style={{ color: "var(--text-secondary)" }}>
              Hard
              <input
                type="number"
                min={60}
                step={60}
                value={hardTimeout}
                onChange={e => setHardTimeout(Number(e.target.value))}
                className="w-full mt-1 px-2 py-1 rounded text-[11px]"
                style={{ background: "var(--bg-panel)", color: "var(--text-primary)", border: "1px solid var(--border)" }}
              />
            </label>
            <button
              onClick={saveTimeouts}
              disabled={saving}
              className="w-full px-2 py-1 text-[10px] rounded transition-colors disabled:opacity-40"
              style={{ background: "var(--amber-bg)", color: "var(--amber)", border: "1px solid var(--amber-dim)" }}
            >
              Save timeouts
            </button>
          </div>
          <div style={{ borderTop: "1px solid var(--border)", marginTop: "8px", paddingTop: "8px" }}>
            <p className="text-[10px] font-semibold uppercase tracking-wider mb-2" style={{ color: "var(--text-dim)" }}>OpenCode</p>
            <label className="block text-[10px] mb-2" style={{ color: "var(--text-secondary)" }}>
              Binary path
              <input
                type="text"
                value={opencodeBin}
                onChange={e => setOpencodeBin(e.target.value)}
                className="w-full mt-1 px-2 py-1 rounded text-[11px]"
                placeholder="C:\Users\you\AppData\Roaming\npm\node_modules\opencode-ai\bin\opencode.exe"
                style={{ background: "var(--bg-panel)", color: "var(--text-primary)", border: "1px solid var(--border)" }}
              />
            </label>
            <p className="text-[10px] mb-2 leading-snug" style={{ color: "var(--text-dim)" }}>
              Use the npm package binary. Do not use .opencode\bin\opencode.exe.
            </p>
            <button
              onClick={saveOpencodeBin}
              disabled={saving}
              className="w-full px-2 py-1 text-[10px] rounded transition-colors disabled:opacity-40"
              style={{ background: "var(--blue-bg)", color: "var(--blue)", border: "1px solid var(--blue-dim)" }}
            >
              Save OpenCode path
            </button>
          </div>
          <div style={{ borderTop: "1px solid var(--border)", marginTop: "8px", paddingTop: "8px" }}>
            <button
              onClick={() => { setOpen(false); onRefresh(); }}
              className="w-full px-2 py-1 text-[10px] rounded transition-colors duration-200"
              style={{ background: "var(--bg-panel)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}
            >
              Refresh
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
