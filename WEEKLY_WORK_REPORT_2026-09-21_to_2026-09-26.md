# AzAWH Weekly Work Report

**Primary contributor:** Mounusha Ram Metti  
**Project:** Arizona Atmospheric Water Harvesting Monitoring System

## Executive summary

This week focused on four areas of the AzAWH platform:

1. **Machine learning:** added a trained water-production forecasting workflow and connected experimental anomaly detection results to the dashboard.
2. **Hardware reliability:** fixed a reader restart race that caused intermittent power-meter zero readings and improved the Raspberry Pi operator interface.
3. **Dashboard quality:** modernized data fetching and tests, improved responsive behavior and charts, added synchronized zoom and volume-unit conversion, and introduced clearer anomaly visualization.
4. **Operational accuracy:** corrected station presence reporting so a station remains Online during a short interruption but automatically becomes Offline after 15 minutes without data.

The week produced substantial, production-oriented progress across the edge-device, backend, ML, CI, and frontend layers rather than isolated UI changes.

## Work completed

### Water-production forecasting

- Added the backend `ml` package.
- Built feature-generation and model-training workflows for water-production forecasting.
- Added the trained scikit-learn model and its metadata.
- Added the required scikit-learn and joblib dependencies.
- Documented the dual-repository production push policy.

### Sensor-reader reliability

- Diagnosed a reader restart race affecting the balance, flow, and power readers.
- Changed reader shutdown so the existing background thread is joined before a replacement reader starts.
- Prevented two reader threads from accessing the same serial device simultaneously.
- Eliminated the resulting periodic power-meter zero drops that previously required restarting the UI.

### Raspberry Pi operator interface

- Added a Live Parameters panel showing temperature, humidity, air velocity, balance weight, voltage, power, energy, and flow rate.
- Connected sensor-health indicators to actual missing/available readings instead of fixed checkmarks.
- Added mouse-wheel scrolling to the operator panel.

### Dashboard modernization

- Introduced Vitest and expanded automated coverage for reusable calculation and chart logic.
- Migrated dashboard data fetching to TanStack Query for shared caching and clearer loading/error states.
- Extracted pure data-transformation functions from page components so they could be tested independently.
- Added peak-preserving chart downsampling so short spikes are not lost in large datasets.
- Added drag-to-zoom for time-series charts.
- Improved chart layouts for phone-sized screens.
- Reworked the header into a responsive mobile drawer.
- Added an application-level error page.
- Cleaned up environment examples and corrected README setup instructions.

### Delivery and quality controls

- Added a GitHub Actions dashboard pipeline covering lint, TypeScript, tests, and production build.
- Updated project statistics to reflect 1.58M+ records and nine deployed stations.

### Usability improvements

- Added liters, US gallons, and acre-feet selection to station pages.
- Applied the selected unit consistently to dashboard values and CSV downloads.
- Corrected the station-page Back button so it returns to `/stations`.

### Experimental anomaly visualization

- Added a batch exporter that runs the saved Isolation Forest ensemble against recent station data.
- Added safeguards for missing sensors, insufficient windows, and stations with an excessive flagged fraction.
- Merged adjacent flagged windows into understandable activity intervals.
- Generated plain-language descriptions based on measured before/during values.
- Added a static anomaly-data query to the dashboard.
- Added tested helpers for matching anomaly parameters, clipping intervals, and aligning shaded regions to plotted points.
- Added an experimental unusual-activity switch, event list, and numbered shaded chart bands.
- Clearly labeled detections as unusual behavior rather than confirmed incidents.

### Runtime review and dashboard verification

- Ran and reviewed the dashboard across multiple sessions.
- Checked chart rendering, station pages, and development-server behavior.
- Investigated chart container-size warnings observed during runtime.
- Focused this work on validation and troubleshooting.

### Synchronized chart zoom

- Lifted chart zoom into shared station-page state.
- Made a zoom selection on any sensor or hourly graph apply to all graphs automatically.
- Allowed zoom to be initiated from line or bar charts.
- Made Reset Zoom restore every graph together.
- Reset zoom when the selected date period changes.
- Changed zoomed Y-axes to use the visible data minimum and maximum instead of forcing the axis to begin at zero.

### Station Online/Offline behavior

- Identified the cause of contradictory statuses: the backend retained `active` status for 48 hours while the UI could already say `Not sending`.
- Replaced the 48-hour window with a 15-minute Online threshold.
- Defined the user-facing states as:
  - **Live / Online:** last reading under 2 minutes old.
  - **Delayed / Online:** last reading from 2 to under 15 minutes old.
  - **Not sending / Offline:** no reading for 15 minutes or more.
- Added a shared freshness clock so statuses change automatically without a page refresh.
- Applied the same rule to station cards, station counts, detail pages, the comparison page, admin/backend responses, and Online-first sorting.
- Reduced the station-list backend cache from five minutes to 30 seconds so recovered stations appear promptly.

### Verification

- Passed TypeScript checks.
- Passed all 44 automated dashboard tests.
- Passed ESLint with no errors; remaining warnings are pre-existing cleanup items.
- Passed backend Python syntax validation.
- Confirmed the local frontend and backend both return HTTP 200.

## Key outcomes

- Water-production forecasting is now represented by a reproducible backend ML workflow and saved model artifact.
- Raspberry Pi sensor readers restart safely without competing serial threads.
- Field operators can see live system parameters and truthful sensor-health states.
- The dashboard has stronger automated testing, CI, responsive behavior, and query caching.
- Large charts preserve peaks and support synchronized detailed investigation.
- Users can work in liters, gallons, or acre-feet, including downloaded data.
- Experimental model-detected unusual activity is visible with appropriate scientific caveats.
- Station presence now matches actual telemetry recency and transitions automatically after a 15-minute outage.

## Repository delivery

The work spans two repositories:

- `az_awh_monitoring_system`: backend, Raspberry Pi, forecasting, anomaly export, CI coordination, and this report.
- `az_awh_dashboard`: dashboard architecture, tests, responsive UI, units, synchronized zoom, anomaly overlays, and station-presence presentation.

Local secrets such as `.env` and `.env.local` are intentionally excluded from version control.
