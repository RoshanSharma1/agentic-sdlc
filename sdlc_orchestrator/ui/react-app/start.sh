#!/bin/bash
set -e

echo "🚀 Starting SDLC React Dashboard"
echo ""

# Check if node_modules exists
if [ ! -d "node_modules" ]; then
    echo "📦 Installing dependencies..."
    npm install
    echo ""
fi

# Start the development server
echo "🔧 Starting development server..."
echo "Dashboard will be available at http://localhost:3000"
echo "Make sure the backend API is running on http://localhost:8765"
echo ""

npm run dev
