# Project Phoenix — Plain-English Overview

*A no-jargon tour of what this project is, what we built, what we proved, and where we stand today.*

**Last updated:** July 2026

---

## 1. What is this project, in one breath?

Project Phoenix is a **computer model of a new kind of hybrid car powertrain** — the
machinery that makes a vehicle move. Instead of building expensive physical prototypes,
we built a **"digital twin"**: a software simulation that obeys real physics (weight, air
drag, rolling resistance, engine efficiency, battery limits) so we can test ideas honestly
on a laptop before anyone bends metal.

The guiding rule of the whole project has been **honesty over hype**: every claim has to
come out of the simulation's own data. If an idea doesn't actually help, we say so — a
"no, that didn't work" is just as valuable as a "yes".

---

## 2. The car's powertrain, explained simply

Think of the vehicle as a **series hybrid** with three energy players:

| Part | Everyday analogy | Job |
|------|------------------|-----|
| **Engine + generator (ATPE)** | A small, super-efficient petrol generator | Makes electricity steadily. It does **not** drive the wheels directly. |
| **Battery** | A medium fuel tank for electricity | Stores energy, smooths things out, drives the wheels when it can. |
| **Inertial buffer (PCMRITMS)** | A spinning flywheel "shock absorber" | Delivers very fast bursts of power for a second or two — like a sprinter's kick. |

Everything feeds a shared "electrical bus" (think of it as a power highway). A **controller**
— the brain — decides, moment to moment, who supplies power and who stores it.

We model **six different vehicle bodies** (SUV, Sedan, Hatchback, Crossover, Pickup, Van)
so we can see how the same technology behaves on a light car versus a heavy truck.

---

## 3. How we work: every change is a measured "Move"

Rather than rebuilding everything at once, we add one realism layer at a time. We call each
one a **"Move"** (A through M, plus a comparison study N). Every Move follows the same discipline:

1. Build the new capability.
2. Make it **opt-in** — it can never quietly worsen numbers we already trust.
3. Prove it with data (an A/B comparison across all six bodies).
4. Lock the result with automated tests so it can never silently break later.

**Phase 1 simulation is complete:** Moves A through M are all built, tested, and demonstrated.
Move N adds a head-to-head comparison against a conventional engine (see below).

There is also a single command — **`verify.py`** — that re-checks every headline number and
runs the whole test suite. Today it confirms **63 checks + 169 tests all pass** (Phoenix V3 /
ATPE Brain tests need design-stack `numpy`). That's our
"nothing is broken" green light. For a faster check (~10 seconds), use **`verify.py --quick`**.

A **one-page executive summary** at the start of `main.py` rolls up the most important numbers
from every Move (including cold weather, full loads, plugging in, ageing, the ICE comparison,
and fault tolerance).

---

## 4. What each Move actually did (in plain words)

### Move A — Give the car a smarter "brain" (AI controller)
We let the controller **learn** good habits from data instead of only following fixed rules.

- **The catch we caught:** early results looked great but were misleading, because the
  battery was quietly draining — like claiming great gas mileage while secretly running
  down a battery you'll have to recharge later. We added a fair-comparison correction (an
  industry-standard "charge-sustaining" adjustment) so every number is apples-to-apples.
- **Result:** the learned brain matched the rule-based one closely and honestly. No magic,
  but a solid, fair foundation.

### Move B — Make the battery age and heat up like a real one
A real battery wears out and warms up. We added that physics.

- **Result (a pleasant surprise):** under normal driving the battery barely heats up
  (about +1 °C) and is worn out by **distance, not heat**. Projected life is **600,000 to
  1.6 million km** — far beyond a car's lifetime. We also ran an abuse test that *does* make
  it overheat, to prove the model genuinely reacts.
- **Plain takeaway:** this battery is **"throughput-bound, not heat-bound."** It lasts.

### Move C — Find out which knobs actually matter
We systematically wiggled each input (weight, air drag, tyre friction, etc.) to see which
ones move fuel use the most.

- **Result:** at highway speed, **aerodynamics matter more than weight**. On other cycles,
  weight matters more. So there is **no single "most important" factor** — it depends on how
  you drive. We also confirmed that a bigger energy buffer reliably removes power shortfalls.
- This Move also created the one-command `verify.py` "prove-it-all" button.

### Move D — Work out the real cost and pollution of ownership
Fuel economy alone doesn't decide if a powertrain is worth building. Owners care about
**total cost over the car's life** and **total CO₂ from cradle to road** (including the
carbon baked into making the battery).

- **Result 1:** the battery is **never replaced** during the car's life on any body. The
  single biggest hidden cost of many electrified cars simply doesn't exist here.
- **Result 2:** the battery's manufacturing CO₂ is only about **1.2 tonnes out of ~26 tonnes**
  total for the SUV — under 5%. A small, hard-working battery pays back its carbon almost
  immediately.
- **Plain takeaway:** the whole money-and-carbon story is carried by **a small battery that
  never needs replacing**, not by squeezing out the last drop of fuel.

| Body | Running cost / km | Lifetime CO₂ (all-in) |
|------|------------------:|----------------------:|
| Hatchback | €0.039 | 44 g/km |
| Sedan | €0.040 | 47 g/km |
| Crossover | €0.048 | 60 g/km |
| AWD SUV | €0.072 | 102 g/km |
| Van / MPV | €0.077 | 112 g/km |
| Pickup | €0.092 | 137 g/km |

### Move E — Try to spend the flywheel's "sprint" more cleverly
The spinning buffer can deliver a big 140 kW burst. We built a smarter controller to spend
that burst only when truly needed, hoping to save the reserve for the launches that need it.

- **Result (an honest "no"):** on every realistic driving test it made **zero difference**.
  Why? Because the buffer runs out of **stored energy** long before its **burst power** is
  ever the limiting factor. The 140 kW number is real, but it's **not the thing that decides
  whether a launch succeeds** — buffer *energy* is.
- We then built a special "stress" scenario where the burst *can* matter, and there the smart
  controller did exactly what it should (used less reserve for the same result) — proving the
  feature works; it just isn't needed in normal driving.
- **Plain takeaway:** a valuable negative finding. We now know **the real lever is buffer
  energy, not burst power** — which tells future designers where to spend money.

### Move F — "So what do we actually build?" (right-sizing)
We finally asked the design question the whole project was building toward: how big do the
parts really need to be? We tested each "knob" one at a time.

- **The clarifying result:** the thing that decides whether the car can handle hard launches
  is the **battery's power output**, not the flywheel burst and not the engine ramp speed.
  (This sharpens Move E: the flywheel's sprint genuinely doesn't change the answer.)
- **The recommendation:** the default design's battery power is **over-specced by 25–50%**.
  Most bodies stay fully capable with **half** the assumed power; only the heavy SUV needs
  more. A lower-power battery is **cheaper and lighter** — a real saving.
- **Importantly:** the battery stays the same small 20 kWh size, so Move D's "small, cheap,
  never-replaced, low-carbon" story is completely unaffected.
- **Plain takeaway:** build a **modest, well-matched battery** plus the flywheel for split-
  second spikes — not the oversized, expensive pack the optimistic default assumed.

| Body | Battery power needed | vs default (120 kW) |
|------|---------------------:|--------------------:|
| Sedan / Hatchback / Pickup / Van | 60 kW | −50% |
| Crossover | 70 kW | −42% |
| AWD SUV | 90 kW | −25% |

### Move G — "How sure are we?" (error bars)
Up to now every result was a single number. But every input behind those numbers is itself a
bit uncertain — the exact weight of the car, the price of fuel ten years from now, what a
battery will cost. So we wiggled **all** of those inputs at once, hundreds of times, and
watched how much each headline number moved. That turns each single figure into a **range**.

- **The point numbers were honest.** For most bodies the original single number lands right in
  the **middle** of its range — we weren't quoting the rosy best case, we were quoting the centre.
- **The fuel figure is tight; the cost of the heavy vehicles is lop-sided.** The SUV's fuel is
  ±10%. But the heavy **Pickup**'s cost range leans toward the expensive side — the same bad
  luck that makes a heavy vehicle heavier hurts it more than it helps the light ones.
- **Plain takeaway:** none of the earlier conclusions changed — Move G just staples an honest
  **error bar** onto each one, so we can say "about 4.5, give or take 10%" instead of pretending
  we know it to three decimals.

| Number (AWD SUV) | Single figure | Honest range (90%) |
|------|---------------:|-------------------:|
| Highway fuel (L/100km) | 4.46 | 4.08 – 4.93 |
| Cost (€/km) | 0.072 | 0.057 – 0.089 |
| Lifetime CO₂ (g/km) | 102 | 91 – 117 |

### Move H — "What about winter and summer?" (ambient temperature)
Every number so far assumed a mild day. Real cars drive at -10 °C and at +40 °C, so we tested
the whole range. Three things change with temperature: cold air is **denser** (more wind
resistance), the cabin needs **heating** when cold and **air-conditioning** when hot, and the
battery sits at whatever the outside temperature is.

- **Fuel use is a U-shape — cheapest around 20 °C, worse in both directions, and worst in the
  cold.** Cold hurts most because dense air *and* cabin heating pile on together. Season to
  season the swing is about **12–23%** — a real penalty the single mild-weather number hid
  (SUV ~12%; light bodies nearer 23%).
- **The battery never overheats — even on a +40 °C day.** It peaks around 42 °C, safely below
  the point where it would have to throttle back. This is the **same** "the pack is comfortably
  over-specced and runs cool" conclusion Move F reached, now confirmed in hot weather too.
- **Plain takeaway:** budget for a noticeable winter fuel penalty, but stop worrying about
  hot-weather battery fade — under normal driving it simply doesn't happen.

### Move I — "How does it do on the official tests?" (WLTP / EPA cycles)
Up to now we drove our own made-up routes. Every production car is actually measured on a few
**official** test drives — Europe's **WLTP** and the US **EPA** city and highway cycles. We
rebuilt those and ran the fleet on them.

- **Honesty note:** we did **not** copy the official second-by-second traces (they're
  copyrighted). We rebuilt routes that match each test's published distance, duration and
  speeds to within a few percent — enough for a fair fuel number, and we say so plainly.
- **The result is reassuringly boring:** the official-style numbers land in the same ballpark
  and the same body-ranking as our home-made routes. In other words, **our made-up cycles were
  representative all along** — nothing we concluded earlier needs revisiting.
- **Every vehicle finishes every official cycle** with power to spare — no shortfalls anywhere.

---

### Move J — "What about a cold start?" (engine warm-up)
A real engine drinks extra fuel for the first minute while it warms up. We added that penalty
on top of the warm result, so no earlier number changes.

- The surprise: on **city driving the penalty is zero**, because the car runs on the battery and
  the engine never starts. The penalty only shows up on **longer trips** where the engine runs.
- **Cold weather makes it worse** — a −10 °C start costs more than a mild one.
- The lightweight bodies show big *percentages* only because their warm fuel use is already tiny.

### Move K — "What if it's full of people and luggage?" (payload)
Every number so far was for a car with just a driver. We loaded it up to five people plus cargo
(about +475 kg) and re-checked.

- Fuel use rises **12–41%** fully loaded — biggest *percentage* on the lightest cars.
- **The car still pulls every hill** fully loaded — capability never fails.

### Move L — "What if you plug it in?" (grid charging)
The battery is big enough to drive a real distance on cheap grid electricity if you charge it.
We compared driving on electricity vs fuel.

- The honest twist: because the hybrid is **already so fuel-efficient**, plugging into a *dirty*
  power grid is barely greener and can even cost a touch more.
- The real win comes (a) for the **thirsty pickup**, and (b) when the **grid is clean** — then
  CO₂ roughly halves or better. So the lever is *how clean your electricity is*, not the plug itself.

### Move M — "How does it age?" (wear over 250,000 km)
Every figure so far was for a brand-new car. We aged the battery and drivetrain to end-of-life.

- **Electric range fades about 23%** over the car's life (the battery slowly shrinks).
- **Fuel use creeps up** — noticeable in percent only on the lightest cars, small in real litres.
- A brand-new car reproduces the validated numbers exactly, so nothing earlier is affected.

### Move N — "Compared to what?" (conventional 2.0 L turbo)
Every efficiency claim needs a fair opponent. We swapped the novel generator for a
**representative conventional 2.0 litre turbo petrol engine** — but kept the **same car, same
battery, same flywheel, same brain, same roads**. The only thing that changes is how fuel
turns into electricity.

- **Result (AWD SUV, mixed driving):** the novel design uses about **2.35 L/100 km** vs the
  conventional engine's **3.26 L/100 km** — roughly **28% less fuel** on the identical trip.
- **Plain takeaway:** the architecture's advantage is not "hybrid vs non-hybrid" in the abstract;
  it is **this specific generator strategy vs a normal engine**, measured honestly in the same
  simulation harness. These are still **model numbers**, not road-test certificates.

**July 2026 model note — weighted tier fuel accounting:** when several cylinder tiers fire together,
the twin now splits electrical output across active tiers (smallest first) and sums fuel per tier,
instead of applying the largest tier's efficiency to all output. That removes an unphysical efficiency
cliff above 110 kW and slightly improves highway/tow fuel figures (~3–4% on the SUV). Peak Tier 1
BTE is still ~46%; the full 8-cylinder stack at rated power is ~41% BTE energy-weighted.

### Reliability — "What if one cylinder fails?"
The engineering spec says the engine should keep working if a single combustion module goes
offline. We modelled that: remove one cylinder from each tier in turn and re-run every
performance check.

- **Result:** all six vehicle types still pass **every** target (acceleration, top speed, hill
  climbing, efficiency) in all 18 fault scenarios tested.
- **Plain takeaway:** the design has **headroom** for graceful degradation in simulation. Real
  hardware still has to prove the same thing on a test rig.

---

## 5. The big-picture conclusions

- **The architecture is sound and honest.** Every claim is reproducible from the model.
- **It beats a conventional engine in a fair fight** — about **28% less fuel** than a 2.0 L
  turbo on the same SUV and the same route (Move N). Always say "simulation shows…"
- **The battery is the quiet hero:** small, cool-running, essentially lifetime-lasting, with
  a tiny carbon footprint — and it never needs replacing.
- **Capability comes from energy and battery power, not flashy peak power.** Three separate
  Moves (C, E and F) reached the same conclusion from different directions.
- **The small battery runs cool in every season** — a +40 °C day never makes it throttle back,
  the same "comfortably over-specced" verdict from a second angle.
- **Loaded, cold, or plugged in, the verdict holds** — a full payload never defeats the hill
  climb, a cold start only costs fuel on longer trips, and plugging in only pays off CO₂ when the
  grid is clean.
- **It ages gracefully** — about 23% electric-range loss over 250,000 km, with fuel creeping up
  only modestly in real terms.
- **One cylinder can fail and the car still passes** — in simulation, every body clears all
  performance targets with a single module offline (fault-tolerance study).
- **The default battery power is over-specced** — right-sizing it saves cost and weight while
  keeping the small, low-carbon pack.
- **Cost and CO₂ are competitive** across everything from a hatchback to a pickup.
- **Every headline number carries an honest error bar** — the single figures are the centre of
  their range, not the optimistic edge.
- **No single "magic" factor** runs the show — good design balances aerodynamics, weight,
  and energy storage depending on how the vehicle is used.

---

## 6. Where we stand today

Think of progress in two parallel tracks: **what the computer can do** (simulation) and **what
has been built in a lab** (hardware).

### Simulation — largely done for Phase 1

| Area | Status in plain English |
|------|-------------------------|
| **Moves A–M** | **Complete** — every planned realism layer is built, tested, and in the demo |
| **Move N (vs normal engine)** | **Complete** — fair head-to-head comparison on identical routes |
| **Six vehicle types** | **All pass** their performance targets in the model |
| **Executive summary** | **One table** at the start of `main.py` covers fuel, cost, CO₂, seasons, cold start, payload, plug-in, ageing, ICE saving, and fault tolerance |
| **Interactive dashboard** | Run `python -m dashboard` to pick a vehicle and watch a drive cycle play back |
| **Integrity check** | `verify.py` → **PASS (63 checks + 169 tests)** |
| **Virtual Gate 1 bench** | **48-cell matrix** in simulation + CSV (`scripts/export_gate1_matrix.py`) — not lab measured |
| **Virtual Gate 4 scaling** | **X4–X16 tier-mix study** + CSV — every micro/medium/large split per ring size |
| **ATPE Brain / ECU** | Gate 6 supervisory brain + Layer-2 ECU in software — not vehicle-certified |

**Bottom line:** the laptop phase of the project is **finished enough to support funding and
engineering conversations**. We are not looking for more simulation "Moves" before building
real parts.

### Hardware — not started yet

Engineering progress is also measured in **seven "Gates"** — milestones from single-cylinder
physics through to a full vehicle on the road. In **software** we are strongest at **Gate 5**
(whole vehicle works in the model). In the **real world**, **no gate has been cleared yet** —
there is no lab rig, no measured efficiency, no test car.

| Gate | What it means (simply) | Computer | Real world |
|:----:|------------------------|----------|------------|
| **1** | Understand one combustion cylinder | Virtual 48-cell bench + opt-in physics | **Not built** |
| **2** | Piston moves stably without crashing | RPM-linked timing + stability surrogate | **Not built** |
| **3** | Piston motion turns into electricity efficiently | Assumed numbers | **Not measured** |
| **4** | Many cylinders work together | Virtual layout sweep + X12 ring model | **Not built** |
| **5** | Whole car on real roads / dyno | **Done in simulation** | **Not built** |
| **6** | Smarter AI control | Supervisory brain + ECU runtime in software | **Not product / lab HIL** |
| **7** | Manufacturable mechanical design | Concept images & storyboard | **No production CAD** |

The **20-panel PHOENIX-X12 storyboard** in the `designs/` folder is the visual "what we want
to build." A few short animations exist; a full marketing video pipeline is **deferred** until
bench data exists or a pitch specifically needs visuals.

### What this means for a non-engineer

- **Trust the simulation for direction, not for certification.** Numbers like 4.6 L/100 km or
  28% fuel saving are **well-disciplined projections**, not EPA or WLTP road-test results.
- **The next credibility step is metal, not more code.** The Seed-phase plan is a **single
  engine cylinder on a lab bench**, then a flywheel bench test, then eventually a test vehicle.
- **Always prefix claims with "simulation shows…"** and point sceptics to `verify.py`.

For a fuller status report (still plain English), see
[PROJECT-STATUS-AND-NEXT-STEPS.md](PROJECT-STATUS-AND-NEXT-STEPS.md). For the detailed gate
scorecard (updated when bench data lands), see the [Gate scorecard](09-atpe-ers-and-insights.md#gate-scorecard)
in the technical ERS document.

### What comes next (recommended order)

1. **Package the evidence** — run `verify.py`, then `scripts/export_evidence_pack.py` (text + Gate 1 CSV matrix).
2. **Use the virtual bench for pre-funding** — 48 simulated cartridge tests in `GATE1-VIRTUAL-BENCH-MATRIX.csv`; say “simulation shows…” not “lab proved.”
3. **Build Gate 1 hardware** — one free-piston cartridge on a lab rig; measure stroke, pressure, power.
4. **Compare rig to model** — update the simulation when measurements disagree.
5. **Optional:** a short hero video from the Blender concept if a meeting needs visuals before bench data.

For the full parallel plan (twin Phase 2 + pitch + patents + Gate 1 rig + 90-day checklist), see
[DEVELOPMENT-PLAYBOOK.md](DEVELOPMENT-PLAYBOOK.md). Regenerate pitch numbers with:

```powershell
.venv\Scripts\python.exe scripts\export_evidence_pack.py
.venv\Scripts\python.exe scripts\export_gate1_matrix.py
.venv\Scripts\python.exe scripts\export_gate4_scaling.py
```

---

## 7. How someone could check our work

Anyone with the project can run **one command** and watch every headline number re-derive
itself from the live code and pass:

```
.venv\Scripts\python.exe verify.py
```

Today that prints **`RESULT: PASS (63 checks + 169 tests)`**. For a quick smoke check:

```
.venv\Scripts\python.exe verify.py --quick
```

There's also a full demo (`python main.py`) that walks through every result described above,
and a shorter demo (`python main.py --quick`) that skips the slowest blocks.

---

*This document is a plain-language companion to the detailed technical write-up in
[09-atpe-ers-and-insights.md](09-atpe-ers-and-insights.md) and the living status report in
[PROJECT-STATUS-AND-NEXT-STEPS.md](PROJECT-STATUS-AND-NEXT-STEPS.md).*
