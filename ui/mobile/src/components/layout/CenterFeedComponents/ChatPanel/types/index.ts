export interface ChatPanelLayoutState {
  panelHeight: number;
  isDragging: boolean;
  expanded: boolean;
  minimized: boolean;
  setPanelHeight: (height: number) => void;
  setIsDragging: (dragging: boolean) => void;
  setExpanded: (expanded: boolean) => void;
  setMinimized: (minimized: boolean) => void;
  handleDragStart: (e: React.MouseEvent) => void;
}