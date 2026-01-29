# 🌊 Arizona Atmospheric Water Harvesting (AzAWH) - Complete Project Guide

**Last Updated**: December 2, 2025

This is a complete guide explaining every file in your water station monitoring website project. Think of this as your "how everything works" manual.

---

## 📋 Table of Contents

1. [Project Overview](#project-overview)
2. [How It Works (Simple Explanation)](#how-it-works-simple-explanation)
3. [Backend Files (FastAPI Server)](#backend-files-fastapi-server)
4. [Frontend Files (Next.js Website)](#frontend-files-nextjs-website)
5. [How Data Flows](#how-data-flows)
6. [Running the Project](#running-the-project)

---

## 🎯 Project Overview

**What is this project?**
A real-time monitoring dashboard for atmospheric water harvesting (AWH) stations across Arizona. It shows:
- Live sensor data (temperature, humidity, water flow, power consumption)
- Interactive charts
- Station locations and status
- Historical data analysis

**Tech Stack:**
- **Backend**: Python FastAPI (handles data and API)
- **Frontend**: Next.js 14 + Material-UI (the website users see)
- **Data**: JSON files with sensor readings (will connect to Firestore later)

---

## 🔍 How It Works (Simple Explanation)

### The Big Picture:

1. **Sensors** at water stations collect data (temperature, humidity, etc.)
2. **Backend (FastAPI)** stores and serves this data through API endpoints
3. **Frontend (Next.js)** displays beautiful charts and cards
4. **You (user)** open browser → see real-time data!

```
Sensors → Backend API → Frontend Website → Your Browser
         (FastAPI)     (Next.js)
```

---

## 🐍 Backend Files (FastAPI Server)

### Location: `/backend/`

#### **1. `main.py`** - The Brain 🧠
**What it does**: Main FastAPI application with all API endpoints

**Key Features**:
- **Dynamic Schema Handling**: Different stations can have different sensors!
  - Test Station 1 (AguaPars): Has flow sensors, no voltage
  - Test Station 2 (Airjoule): Has voltage/current, no flow
- **API Endpoints**:
  - `GET /stations` → List all stations
  - `GET /stations/{name}/readings` → Get sensor data for a station
  - `POST /export` → Download data as CSV/JSON/Parquet
  - `GET /health` → Check if server is running

**Important Code**:
```python
# Creates flexible Pydantic models that accept any fields
def create_reading_model():
    return create_model('StationReading',
        station_name=(str, ...),
        timestamp=(str, ...),
        unit=(str, ...),
        # All sensor fields are Optional!
        temperature=(Optional[float], None),
        humidity=(Optional[float], None),
        # ... and 12 more optional fields
    )
```

**Why dynamic models?**
Each station has different sensors. This lets us handle any combination without breaking!

---

#### **2. `models.py`** - Data Structures 📊
**What it does**: Defines what data looks like (TypeScript interfaces, but in Python)

**Main Models**:
```python
class StationMetadata(BaseModel):
    station_name: str
    available_fields: List[str]  # What sensors this station has
    field_groups: Dict[str, List[str]]
    last_reading: Optional[str]
    total_readings: int

class StationInfo(BaseModel):
    station_name: str
    unit: str  # e.g., "AguaPars", "Airjoule"
    location: Optional[str]
    status: str  # "active" or "offline"
    metadata: StationMetadata
```

**Think of it as**: The contract for what data can exist in your system.

---

#### **3. `config.py`** - Settings ⚙️
**What it does**: Configuration and settings

**Contains**:
```python
CORS_ORIGINS = ["http://localhost:3000"]  # Allow frontend to talk to backend
DATA_DIR = "data"  # Where test data lives
REDIS_ENABLED = False  # Caching (not active yet)
```

**Why separate file?**
Easy to change settings without touching main code!

---

#### **4. `requirements.txt`** - Dependencies 📦
**What it does**: Lists all Python packages needed

```
fastapi==0.115.5        # Web framework
uvicorn==0.32.1         # Server to run FastAPI
pydantic==2.10.3        # Data validation
redis==5.2.0            # Caching (future)
pandas==2.2.3           # Data manipulation
pyarrow==18.1.0         # Parquet export
google-cloud-firestore  # Database (future)
```

**Install with**: `pip install -r requirements.txt`

---

#### **5. Test Data Files**
**Location**: Should be in `/backend/data/` but currently missing!

**Expected files**:
- `test-station-1.json` → Readings for AguaPars unit
- `test-station-2.json` → Readings for Airjoule unit

**Sample structure**:
```json
{
  "data": [
    {
      "station_name": "test-station-1",
      "timestamp": "2025-11-01T00:00:00Z",
      "unit": "AguaPars",
      "temperature": 28.5,
      "humidity": 45.2,
      "flow_lmin": 5.8,
      "power": 850.2
    }
  ]
}
```

---

## 🌐 Frontend Files (Next.js Website)

### Location: `/water-station-dashboard/`

---

### **Core Application Files**

#### **1. `src/app/layout.tsx`** - Page Wrapper 🎁
**What it does**: Wraps every page with header, footer, and theme

```tsx
export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <head>...</head>
      <body>
        <ClientWrapper>  {/* Adds header, footer, theme */}
          {children}  {/* Your actual page content */}
        </ClientWrapper>
      </body>
    </html>
  );
}
```

**Think of it as**: The picture frame around every page.

---

#### **2. `src/app/page.tsx`** - Homepage 🏠
**What it does**: First page users see with station cards

**Flow**:
1. Loads → Shows spinner
2. Calls API → `GET /stations`
3. Gets data → Maps to card format
4. Displays → Grid of station cards

**Key Code**:
```tsx
useEffect(() => {
  const data = await apiClient.getStations();
  setStations(data);
}, []);

// Maps API data to card format
const stationCards = stations.map(station => ({
  id: station.station_name,
  name: station.station_name,
  location: station.location || 'Arizona, USA',
  status: station.status === 'active' ? 'Online' : 'Offline'
}));
```

**User sees**:
- Hero section: "Arizona Atmospheric Water Harvesting"
- Grid of station cards with:
  - Station name
  - Location
  - Online/Offline status
  - Unit type

---

#### **3. `src/app/stations/[id]/page.tsx`** - Station Details 📈
**What it does**: Detailed view with charts when you click a station

**The `[id]` means**: Dynamic route! 
- `/stations/test-station-1` → Shows test-station-1 data
- `/stations/test-station-2` → Shows test-station-2 data

**Major Sections**:

**A. Data Fetching**:
```tsx
const stationName = params.id;  // Get from URL

// Fetch station info
const stations = await apiClient.getStations();
const station = stations.find(s => s.station_name === stationName);

// Fetch readings
const readings = await apiClient.getStationReadings(stationName);
```

**B. Parameter Selection**:
- User picks date range
- User picks unit (if multiple units)
- User picks category (Intake Air, Water Production, etc.)
- User picks parameters (max 2 for chart)

**C. Chart Data Processing**:
```tsx
// Filter by date and selected parameters
const chartData = readings
  .filter(r => date >= startDate && date <= endDate)
  .map(reading => ({
    date: reading.timestamp,
    value: reading[selectedParameter]
  }));
```

**User sees**:
- Station header with photo and metadata
- Filter controls (date, parameters)
- Interactive line chart
- Demo button (loads sample data)

---

### **Component Files** (Reusable UI Pieces)

#### **4. `src/components/ClientWrapper.tsx`** - Theme Provider 🎨
**What it does**: Provides Material-UI theme and layout structure

**Contains**:
- ASU Maroon (#901340) and Golden (#ffcb25) colors
- Font settings
- Header and Footer wrapper
- Date picker provider

**Why needed?**
Material-UI needs a theme provider to style components.

---

#### **5. `src/components/Header.tsx`** - Top Navigation 🧭
**What it does**: Navigation bar at top of every page

**Features**:
- ASU logo
- "AWH Testbeds" title
- Navigation links: Home, About, Contact
- Responsive (hamburger menu on mobile)

**Navigation routes**:
- Home → `/`
- About → `/about`
- Contact → `/contact`

---

#### **6. `src/components/Footer.tsx`** - Bottom Bar 👣
**What it does**: Footer with links and copyright

**Contains**:
- Links to privacy, terms, documentation
- Copyright: "© 2025 ASU AzAWH"
- Responsive layout

---

#### **7. `src/components/StationCard.tsx`** - Station Cards 🃏
**What it does**: Individual card for each station on homepage

**Props**:
```typescript
{
  name: "test-station-1",
  location: "Arizona, USA",
  status: "Online",
  units: ["AguaPars"]
}
```

**Displays**:
- Station image (random from picsum.photos)
- Station name (bold)
- Location with pin emoji
- Status chip (green=Online, red=Offline)
- Unit type
- Description
- "View Details" button

**Interaction**:
Click → Navigate to `/stations/{name}`

---

#### **8. `src/components/FeaturePlot.tsx`** - Chart Component 📊
**What it does**: Line chart for visualizing sensor data over time

**Uses**: Recharts library for interactive charts

**Features**:
- Responsive (adjusts to screen size)
- Hover tooltips
- Color-coded by parameter type
- Date formatting on X-axis
- Value formatting on Y-axis

**Gets data like**:
```typescript
[
  { date: "2025-11-01", value: 28.5 },
  { date: "2025-11-02", value: 29.1 },
  // ... more points
]
```

---

#### **9. `src/components/CSVExport.tsx`** - Export Button 📥
**What it does**: Download data as CSV file

**Currently**: Temporarily disabled (commented out in page.tsx)
**Reason**: Needs refactoring to work with API data structure

**Will do**:
- Preview data in table dialog
- Export as CSV file
- Filename: `{station}_{parameter}_{dateRange}.csv`

---

### **Library and Type Files**

#### **10. `src/lib/api-client.ts`** - API Communication 📡
**What it does**: All HTTP requests to backend in one place

**Think of it as**: The messenger between frontend and backend

**Main Functions**:

```typescript
// 1. Health check
async health() → checks if backend is alive

// 2. Get all stations
async getStations() → returns StationInfo[]

// 3. Get readings for one station
async getStationReadings(stationName, params?) 
  → returns ReadingsResponse with data[]

// 4. Export data
async exportData(request) → downloads CSV/JSON/Parquet
```

**Why separate file?**
Reuse API calls anywhere, change API once if backend changes!

---

#### **11. `src/types/index.ts`** - TypeScript Types 📝
**What it does**: Defines data structures for TypeScript

**Main Types**:

```typescript
// Station card on homepage
interface Station {
  id: string | number;
  name: string;
  location?: string;
  status: 'Online' | 'Offline';
  units?: string[];
}

// Chart data point
interface ChartDataPoint {
  date: string;
  value: number;
}
```

**Why needed?**
TypeScript checks types → catches bugs before running!

---

#### **12. `src/data/constants.ts`** - Sample Data 🗂️
**What it does**: Legacy file with hardcoded sample stations

**Currently**: NOT USED! App now uses real API data

**Contains**:
- 4 sample stations (CHP, Greenhouse, SRP, Mobile)
- Dummy sensor data generator
- Helper functions

**Will delete**: Once fully migrated to API

---

### **Configuration Files**

#### **13. `package.json`** - Project Dependencies 📦
**What it does**: Lists all npm packages and scripts

**Key dependencies**:
```json
{
  "next": "15.0.3",              // Framework
  "@mui/material": "^6.1.7",      // UI components
  "recharts": "^2.14.1",          // Charts
  "framer-motion": "^11.11.11",   // Animations
  "date-fns": "^4.1.0",           // Date formatting
  "papaparse": "^5.4.1"           // CSV parsing
}
```

**Scripts**:
```json
{
  "dev": "next dev",      // Start dev server
  "build": "next build",  // Build for production
  "start": "next start"   // Run production
}
```

---

#### **14. `next.config.ts`** - Next.js Settings ⚙️
**What it does**: Configures Next.js behavior

**Current settings**:
```typescript
{
  reactStrictMode: true,  // Strict mode for debugging
  images: {
    domains: ['picsum.photos', 'images.unsplash.com']
  }
}
```

---

#### **15. `tsconfig.json`** - TypeScript Settings 🔧
**What it does**: Configures TypeScript compiler

**Key settings**:
```json
{
  "compilerOptions": {
    "target": "ES2020",
    "strict": true,           // Strict type checking
    "paths": {
      "@/*": ["./src/*"]      // Import alias: @/components
    }
  }
}
```

---

#### **16. `src/app/globals.css`** - Global Styles 🎨
**What it does**: CSS that applies to entire site

**Contains**:
```css
:root {
  --background: #ffffff;
  --foreground: #171717;
}

body {
  background: var(--background);
  color: var(--foreground);
}
```

---

### **Static Files**

#### **17. `public/` folder** - Images and Assets 🖼️
**What it does**: Stores static files served directly

**Files**:
- `asu_logo.png` → ASU logo in header
- `favicon.ico` → Browser tab icon
- `*.svg` → Various icons (Next.js defaults)

**Access**: `public/asu_logo.png` → `/asu_logo.png` in browser

---

### **Other Pages**

#### **18. `src/app/about/page.tsx`** - About Page ℹ️
**What it does**: Information about the project

**Shows**:
- Project description
- Research goals
- Team information

---

#### **19. `src/app/contact/page.tsx`** - Contact Page 📧
**What it does**: Contact form or information

**Shows**:
- Email addresses
- Contact form
- Location information

---

## 🔄 How Data Flows

### Complete Flow (User clicks station card):

```
1. USER CLICKS CARD
   ↓
2. BROWSER navigates to /stations/test-station-1
   ↓
3. NEXT.JS renders page.tsx with params.id = "test-station-1"
   ↓
4. REACT useEffect runs
   ↓
5. API CLIENT calls apiClient.getStations()
   ↓
6. FETCH requests http://localhost:8000/stations
   ↓
7. FASTAPI main.py handles request
   ↓
8. PYTHON reads JSON file from data/
   ↓
9. FASTAPI returns JSON response
   ↓
10. API CLIENT parses response
   ↓
11. REACT updates state with data
   ↓
12. REACT re-renders with station info
   ↓
13. USER SEES station details!
```

### Data Transformation Example:

**Backend sends**:
```json
{
  "station_name": "test-station-1",
  "unit": "AguaPars",
  "location": "Arizona, USA",
  "status": "active",
  "metadata": {
    "available_fields": ["temperature", "humidity", "flow_lmin"],
    "total_readings": 10
  }
}
```

**Frontend transforms to**:
```typescript
{
  id: "test-station-1",           // For routing
  name: "test-station-1",
  location: "Arizona, USA",
  status: "Online",               // active → Online
  units: ["AguaPars"]
}
```

---

## 🚀 Running the Project

### Backend (FastAPI):

```bash
# 1. Go to backend folder
cd backend

# 2. Activate virtual environment
source venv/bin/activate

# 3. Run server
python main.py

# Server starts at: http://localhost:8000
```

**Test it**:
```bash
curl http://localhost:8000/health
# Should return: {"status": "healthy"}
```

---

### Frontend (Next.js):

```bash
# 1. Go to frontend folder
cd water-station-dashboard

# 2. Install dependencies (first time only)
npm install

# 3. Run dev server
npm run dev

# Server starts at: http://localhost:3000
```

**Open browser**: http://localhost:3000

---

## 🐛 Common Issues

### Issue 1: "Backend not running"
**Error**: Frontend shows "Failed to fetch stations"
**Fix**: Make sure backend is running on port 8000

### Issue 2: "No data showing"
**Error**: Charts are empty
**Cause**: Missing test data files in backend/data/
**Fix**: Create test-station-1.json and test-station-2.json

### Issue 3: "CORS error"
**Error**: "Access blocked by CORS policy"
**Fix**: Check config.py has correct CORS_ORIGINS

### Issue 4: "Port already in use"
**Error**: "Address already in use"
**Fix**: Kill existing process:
```bash
# Kill backend
pkill -f "python main.py"

# Kill frontend
pkill -f "next dev"
```

---

## 📊 Current Status

### ✅ Working:
- Backend API with dynamic schemas
- Frontend UI with Material-UI
- Homepage with station cards
- Station details page
- API client integration
- Type-safe TypeScript
- Responsive design

### ⏳ In Progress:
- Test data files setup
- CSV export functionality

### 🔜 Future:
- Firestore integration
- Redis caching
- WebSocket real-time updates
- User authentication
- Historical data analysis
- Mobile app

---

## 🎓 Learning Resources

**Understanding the stack**:
- **FastAPI**: https://fastapi.tiangolo.com/
- **Next.js**: https://nextjs.org/docs
- **Material-UI**: https://mui.com/material-ui/
- **TypeScript**: https://www.typescriptlang.org/docs/

**Key concepts**:
- **REST API**: How backend and frontend communicate
- **Dynamic Routing**: `/stations/[id]` accepts any station name
- **Type Safety**: TypeScript prevents bugs
- **Responsive Design**: Works on phone, tablet, desktop

---

## 💡 Key Takeaways

1. **Backend is flexible**: Can handle any sensor combination
2. **Frontend is type-safe**: TypeScript catches errors early
3. **Data flows one way**: Backend → API → Frontend → Browser
4. **Components are reusable**: Write once, use everywhere
5. **Everything is documented**: You can understand any file!

---

**Questions?** Check the README files in each folder or ask your team! 🚀
