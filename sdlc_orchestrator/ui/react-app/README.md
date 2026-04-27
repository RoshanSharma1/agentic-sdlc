# SDLC Dashboard - React Application

A modern, modular React-based UI for the SDLC Orchestrator.

## Features

- **Modular Component Architecture**: Clean separation of concerns with reusable components
- **TypeScript**: Full type safety across the application
- **Modern React**: Hooks-based functional components
- **Responsive Design**: Mobile-first approach with full responsive support
- **Real-time Updates**: Auto-refreshing project data
- **Chat Interface**: Interactive chat with AI agents
- **Multiple Views**: Projects, Pipeline, and Agents views

## Project Structure

```
react-app/
├── src/
│   ├── components/       # Reusable UI components
│   │   ├── Chat/         # Chat-related components
│   │   ├── Header.tsx
│   │   ├── ViewTabs.tsx
│   │   └── ProjectCard.tsx
│   ├── views/            # Main view components
│   │   ├── ProjectsView.tsx
│   │   ├── PipelineView.tsx
│   │   └── AgentsView.tsx
│   ├── hooks/            # Custom React hooks
│   │   ├── useProjects.ts
│   │   ├── useProject.ts
│   │   └── useChat.ts
│   ├── services/         # API service layer
│   │   └── api.ts
│   ├── types/            # TypeScript type definitions
│   │   └── index.ts
│   ├── styles/           # Global styles
│   │   └── global.css
│   ├── App.tsx           # Main application component
│   └── main.tsx          # Application entry point
├── public/               # Static assets
├── package.json
├── vite.config.ts
└── tsconfig.json
```

## Installation

```bash
# Install dependencies
npm install
```

## Development

```bash
# Start development server (requires backend API running on port 8765)
npm run dev
```

The app will be available at `http://localhost:3000`

## Building for Production

```bash
# Create production build
npm run build

# Preview production build
npm run preview
```

## API Integration

The React app connects to the FastAPI backend running on port 8765. Make sure the backend server is running before starting the React app.

The Vite development server proxies `/api` requests to `http://localhost:8765`.

## Key Components

### Views

- **ProjectsView**: Grid display of active and closed projects
- **PipelineView**: Kanban-style board showing projects by phase
- **AgentsView**: Agent registry and management interface

### Components

- **Header**: Application header with refresh controls and runtime info
- **ViewTabs**: Navigation between different views
- **ProjectCard**: Card component displaying project information
- **ChatButton**: Floating action button to open chat
- **ChatOverlay**: Chat interface overlay

### Hooks

- **useProjects**: Fetches and manages project list with auto-refresh
- **useProject**: Fetches detailed project data
- **useChat**: Manages chat state and message polling

### Services

- **api.ts**: Centralized API client with typed endpoints for:
  - Projects management
  - Chat interactions
  - GitHub integration
  - Filesystem browsing
  - Agent operations

## Styling

The application uses CSS modules and custom CSS for styling, with a design system based on:

- Custom CSS variables for theming
- Responsive breakpoints
- Modern gradients and shadows
- Smooth animations and transitions

## Type Safety

Full TypeScript coverage with strict mode enabled:
- Type definitions for all API responses
- Strongly typed component props
- Type-safe hooks and services

## Browser Support

Supports all modern browsers:
- Chrome/Edge (latest 2 versions)
- Firefox (latest 2 versions)
- Safari (latest 2 versions)

## License

Part of the SDLC Orchestrator project.
