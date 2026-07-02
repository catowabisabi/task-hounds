import { useState, useEffect, useRef, useCallback } from "react";
import type { ChatPanelLayoutState } from "../types";

export function useChatPanelState(initialHeight = 128): ChatPanelLayoutState {
  const [panelHeight, setPanelHeight] = useState(initialHeight);
  const [isDragging, setIsDragging] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [minimized, setMinimized] = useState(false);
  const dragStartYRef = useRef(0);
  const dragStartHeightRef = useRef(initialHeight);

  const handleDragStart = useCallback((e: React.MouseEvent) => {
    setExpanded(false);
    setIsDragging(true);
    dragStartYRef.current = e.clientY;
    dragStartHeightRef.current = panelHeight;
    e.preventDefault();
  }, [panelHeight]);

  useEffect(() => {
    if (!isDragging) return;

    const handleMouseMove = (e: MouseEvent) => {
      const delta = dragStartYRef.current - e.clientY;
      const newHeight = Math.min(300, Math.max(40, dragStartHeightRef.current + delta));
      setPanelHeight(newHeight);
    };

    const handleMouseUp = () => {
      setIsDragging(false);
    };

    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);

    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    };
  }, [isDragging]);

  return {
    panelHeight,
    isDragging,
    expanded,
    minimized,
    setPanelHeight,
    setIsDragging,
    setExpanded,
    setMinimized,
    handleDragStart,
  };
}