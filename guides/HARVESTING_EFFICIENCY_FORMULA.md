# Harvesting Efficiency Formula

## Purpose
Harvesting efficiency answers one question:

How much water did the AWH system actually collect compared to how much water was available in the air that passed through the duct during the same time interval?

---

## Clear Formula
For each reading i:

$$
\eta_i (\%) =
\begin{cases}
\min\left(\dfrac{\Delta W_i}{W_{\text{intake},i}} \times 100,\ 100\right), & \text{if inputs are valid and } W_{\text{intake},i}>0 \\
0, & \text{otherwise}
\end{cases}
$$

Where:

$$
\Delta W_i = \max(W_i - W_{i-1},\ 0)
$$

$$
W_{\text{intake},i} = AH_i \times v_{\text{m/s},i} \times A \times \Delta t_i
$$

$$
A = 0.18\ \text{m}^2
$$

---

## Hourly Harvesting Efficiency Formula
For each hour h, the system now also computes a true hourly efficiency:

$$
\eta_h (\%) =
\begin{cases}
\min\left(\dfrac{\sum_{i \in h} \Delta W_i^{+}}{\sum_{i \in h} W_{\text{intake},i}} \times 100,\ 100\right), & \text{if } \sum_{i \in h} W_{\text{intake},i} > 0 \\
0, & \text{otherwise}
\end{cases}
$$

Where:

$$
\Delta W_i^{+} = \max(W_i - W_{i-1},\ 0)
$$

$$
W_{\text{intake},i} = AH_i \times v_{\text{m/s},i} \times A \times \Delta t_i
$$

This means hourly efficiency is not a simple average of point efficiencies. It is a ratio of hourly totals:

- total water captured in that hour
- divided by total water available in incoming air during that hour

This is more stable and less noisy than per-reading efficiency.

---

## Absolute Humidity Sub-Formula
Absolute humidity is computed from temperature and relative humidity:

$$
e_s(T_i) = 6.112 \cdot \exp\left(\frac{17.67\,T_i}{T_i + 243.5}\right)
$$

$$
AH_i = \frac{216.7 \cdot (RH_i/100) \cdot e_s(T_i)}{273.15 + T_i}
$$

Units:
- $AH_i$: g/m^3
- $T_i$: deg C
- $RH_i$: %

---

## Velocity Handling (Important)
Velocity must be used in m/s inside the intake water formula.

If incoming data is in another unit, convert first:
- km/h to m/s: divide by 3.6
- mph to m/s: divide by 2.23694
- ft/s to m/s: divide by 3.28084
- ft/min to m/s: divide by 196.850394

The dashboard now normalizes velocity to m/s before calculating harvesting efficiency.

---

## Time Interval Handling

$$
\Delta t_i =
\begin{cases}
30\ \text{s}, & i=0 \\
\min\left(t_i - t_{i-1},\ 120\ \text{s}\right), & i>0
\end{cases}
$$

This cap avoids inflated intake-air estimates during long telemetry gaps.

For hourly efficiency, these same per-interval terms are summed within each hour bucket.

---

## Technical Explanation
1. Compute moisture density in air (absolute humidity) from temperature and RH.
2. Convert velocity to m/s.
3. Compute theoretical incoming water mass for the time step:
   moisture density times volumetric air throughput times time.
4. Compute actual collected water from positive weight delta only.
5. Efficiency is actual divided by theoretical, multiplied by 100.
6. Apply safeguards:
   - negative weight deltas are clipped to 0
   - invalid or missing inputs yield 0
   - efficiency is capped at 100
   - values are rounded for display/export

---

## Non-Technical Explanation
Think of air as a moving stream carrying tiny amounts of water vapor.

- The formula first estimates how much water vapor passed through the machine in that minute.
- Then it checks how much liquid water the machine actually collected.
- If the machine collected half of what was available, efficiency is about 50%.
- If airflow is zero or key sensor values are missing, efficiency is shown as 0 because there is not enough valid information to compute capture performance.

In simple terms:

Harvesting efficiency is the machine score for how well it turns available moisture in incoming air into collected water.

For hourly efficiency, think of it as a report-card score for the entire hour, not for one single reading. This smooths out spikes caused by sensor steps.

---

## Known Practical Limits
- Sensor noise and weight resolution can create many short intervals with zero incremental water.
- Unsynchronized sensor timestamps can add jitter in per-reading efficiency.
- Atmospheric pressure is not currently included in the model (standard practice for this deployment, small error impact).

---

## Variables Summary
- $W_i$: current weight reading (g)
- $W_{i-1}$: previous weight reading (g)
- $\Delta W_i$: incremental produced water (g)
- $AH_i$: absolute humidity (g/m^3)
- $v_{\text{m/s},i}$: intake air velocity normalized to m/s
- $A$: duct area (0.18 m^2)
- $\Delta t_i$: interval in seconds (capped at 120 s)
- $W_{\text{intake},i}$: theoretical water in incoming air (g)
- $\eta_i$: harvesting efficiency (%)
- $\eta_h$: hourly harvesting efficiency (%)
