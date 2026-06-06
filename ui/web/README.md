# Task Hounds Web Dashboard

A modern, responsive web dashboard for monitoring and controlling the Task Hounds multi-agent orchestration system.

## Features

- **Real-time Agent Monitoring**: View status of Manager, Worker, and Reviewer agents
- **Task Queue Management**: Browse, filter, and manage task suggestions
- **Dark/Light Mode**: Toggle between dark and light themes
- **Responsive Design**: Works on desktop, tablet, and mobile devices
- **Modern UI**: Built with React, Tailwind CSS, and shadcn/ui design patterns

## Tech Stack

- **Framework**: React 18 + TypeScript
- **Build Tool**: Vite
- **Styling**: Tailwind CSS
- **Icons**: Lucide React
- **Utilities**: clsx, tailwind-merge

## Getting Started

### Prerequisites

- Node.js 18+
- npm or yarn

### Installation

```bash
cd frontend
npm install
```

### Development

```bash
npm run dev
```

The dashboard will be available at `http://localhost:5173` (or next available port).

### Build for Production

```bash
npm run build
npm run preview
```

## Project Structure

```
frontend/
├── src/
│   ├── App.tsx              # Main application component
│   ├── main.tsx             # Entry point
│   ├── index.css            # Global styles & design tokens
│   └── lib/
│       └── utils.ts         # Utility functions (cn helper)
├── public/                  # Static assets
├── index.html               # HTML template
├── tailwind.config.js       # Tailwind configuration
├── postcss.config.js        # PostCSS configuration
├── vite.config.ts           # Vite configuration
└── package.json
```

## Design System

The dashboard uses a professional design system with:

### Colors
- **Primary**: Electric blue (#3b82f6) - Main brand color
- **Success**: Green (#22c55e) - Positive states
- **Warning**: Amber (#f59e0b) - Active/busy states
- **Destructive**: Red (#ef4444) - Error states

### Theme
- **Light Mode**: Clean, professional appearance with subtle shadows
- **Dark Mode**: Deep navy background with electric blue accents

### Components
- Status badges with color-coded states
- Stat cards with trend indicators
- Agent cards with gradient headers
- Task rows with hover effects

## API Integration (TODO)

Currently using mock data. To connect to the real Task Hounds backend:

1. Update `src/App.tsx` to fetch from your API endpoints
2. Add WebSocket connection for real-time updates
3. Implement authentication if needed

Example API endpoints:
```typescript
GET  /api/agents          # List all agents
GET  /api/suggestions     # List task queue
POST /api/suggestions     # Create new task
GET  /api/stats           # Dashboard statistics
```

## Customization

### Adding New Pages

1. Add new tab in `App.tsx`:
```typescript
const tabs = [
  // ... existing tabs
  { id: 'analytics', label: 'Analytics', icon: BarChart },
]
```

2. Add content in the main section:
```typescript
{activeTab === 'analytics' && (
  <div>Your analytics content here</div>
)}
```

### Modifying Colors

Edit `src/index.css` to change design tokens:
```css
:root {
  --primary: 221 83% 53%;  /* HSL values */
}
```

### Adding New Components

Create components in `src/components/` and import them in `App.tsx`.

## Future Enhancements

- [ ] Real-time WebSocket updates
- [ ] Push notifications
- [ ] Advanced filtering and search
- [ ] Export reports (CSV, PDF)
- [ ] Mobile app (React Native)
- [ ] Charts and analytics
- [ ] Agent configuration panel
- [ ] Task history and logs

## License

MIT
