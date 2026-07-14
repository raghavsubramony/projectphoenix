import {
  BarChart,
  Callout,
  Card,
  CardBody,
  CardHeader,
  Divider,
  Grid,
  H1,
  H2,
  H3,
  Pill,
  Row,
  Stack,
  Stat,
  Table,
  Text,
} from "cursor/canvas";

/**
 * Full digital-twin pack snapshot — 2026-07-14 run
 * Source: docs/evidence-pack/_runlogs/full_pack_20260714_083750.log
 * All figures are simulation outputs unless marked measured.
 */
export default function DigitalTwinPackSnapshot() {
  return (
    <Stack gap={24}>
      <Stack gap={8}>
        <H1>Digital twin pack — live numbers</H1>
        <Row gap={8} align="center" wrap>
          <Pill tone="success" active>
            OVERALL PASS
          </Pill>
          <Pill tone="success">63 checks</Pill>
          <Pill tone="success">146 tests</Pill>
          <Text tone="secondary" size="small">
            Source: full_pack_20260714_083750 · py -3 · ~9.1 min wall
          </Text>
        </Row>
      </Stack>

      <Callout tone="info" title="What this run is">
        Regenerated the evidence pack end-to-end: baseline + Gate 1/4 exports,
        PCMRITMS brain A/B + stress, Gate-6 VV/fleet/endurance, then verify.py.
        Fixed ICEPowertrain missing _use_dynamic_ring (was breaking the pack).
        .venv lacks numpy — pack runner uses the system py -3 launcher.
      </Callout>

      <Grid columns={4} gap={12}>
        <Stat value="PASS" label="Pack overall" tone="success" />
        <Stat value="4.46" label="SUV hwy L/100km" tone="success" />
        <Stat value="27.9%" label="ATPE vs ICE mixed" tone="success" />
        <Stat value="+8–10%" label="Brain fuelEqBuf (stress)" tone="success" />
      </Grid>

      <Divider />

      <H2>Fleet headlines (EVIDENCE-BASELINE)</H2>
      <Text tone="secondary" size="small">
        Phase-1 six-body medians · simulation · AWD SUV mixed ATPE vs 2.0L turbo
      </Text>
      <Table
        headers={[
          "Body",
          "Fuel L/100",
          "EUR/km",
          "CO2 g/km",
          "WLTP L/100",
          "Season swing",
        ]}
        rows={[
          ["AWD SUV", "2.61", "0.072", "102.1", "6.07", "11.6%"],
          ["Sedan", "0.64", "0.040", "46.8", "3.91", "22.3%"],
          ["Hatchback", "0.55", "0.039", "44.2", "3.60", "22.7%"],
          ["Crossover", "1.11", "0.048", "59.9", "4.87", "20.2%"],
          ["Pickup", "3.86", "0.092", "137.2", "7.83", "14.8%"],
          ["Van / MPV", "2.95", "0.077", "111.8", "6.59", "16.6%"],
        ]}
      />

      <Grid columns={3} gap={12}>
        <Card>
          <CardHeader>PCMRITMS rotor</CardHeader>
          <CardBody>
            <Stack gap={6}>
              <Text>Peak 242.8 N·m (+34.9%)</Text>
              <Text>Surge 50.2 kW → buffer 140 kW burst</Text>
              <Text>Stored 0.118 MJ</Text>
            </Stack>
          </CardBody>
        </Card>
        <Card>
          <CardHeader>Battery longevity</CardHeader>
          <CardBody>
            <Stack gap={6}>
              <Text>0.466 EFC / 100 km</Text>
              <Text>Projected pack life 858k km</Text>
              <Text>Peak pack temp 26.3 °C (&lt; 45 °C derate)</Text>
            </Stack>
          </CardBody>
        </Card>
        <Card>
          <CardHeader>ATPE vs ICE (SUV mixed)</CardHeader>
          <CardBody>
            <Stack gap={6}>
              <Text>ATPE 2.35 → ICE 3.26 L/100km</Text>
              <Text>27.9% fuel saving</Text>
              <Text>CO₂ 54 → 75 g/km</Text>
            </Stack>
          </CardBody>
        </Card>
      </Grid>

      <H3>ATPE vs ICE fuel L/100km (identical stack)</H3>
      <Table
        headers={["Body", "Urban", "Highway", "Tow+grade", "Mixed save"]}
        rows={[
          ["AWD SUV", "0→0", "4.46→7.67", "6.91→11.92", "−28%"],
          ["Sedan", "0→0", "0.83→1.42", "4.64→9.21", "−38%"],
          ["Hatchback", "0→0", "0.69→1.19", "4.09→8.47", "−38%"],
          ["Crossover", "0→0", "1.50→2.43", "5.58→10.37", "−31%"],
          ["Pickup", "0→0", "6.17→9.68", "8.58→13.62", "−40%"],
          ["Van / MPV", "0→0", "5.01→8.29", "7.39→12.42", "−46%"],
        ]}
        columnAlign={["left", "right", "right", "right", "right"]}
      />
      <Text tone="secondary" size="small">
        Urban 0 L = pure-EV across all bodies. Negative % = ATPE burned less
        fuel than ICE.
      </Text>

      <Divider />

      <H2>Gate 1 / Gate 4 / verify</H2>
      <Grid columns={4} gap={12}>
        <Stat value="48/48" label="Gate1 matrix PASS" tone="success" />
        <Stat value="4.46" label="Gate4 ref hwy L/100" tone="success" />
        <Stat value="114/177" label="Gate4 ERS passers" />
        <Stat value="7/1/0" label="X8 design sweet spot" />
      </Grid>
      <Table
        headers={["Check", "Result"]}
        rows={[
          ["Gate1 sweet-spot criteria", "6/6 PASS"],
          ["Gate1 uncertainty band (η)", "0.50 ± 0.01 · 90% CI [0.47, 0.51]"],
          ["Gate4 reference 4/2/2 ERS", "9/9 · 230 kW rated"],
          ["Gate4 best-ranked layout", "1/2/1 · hwy 4.58 L/100km"],
          ["Gate4 score band", "ref 889.8 vs best 899.5"],
          ["X8 storyboard pass mixes", "36/45"],
          ["Single-tier full ERS", "0 passers"],
          ["Three-tier full ERS", "1/2/1 PASS"],
          ["X12 design-aligned pick", "10/1/1"],
          ["Fault: 1 cyl offline", "all bodies 9/9 ERS"],
          ["verify.py", "63 checks + 146 tests PASS"],
        ]}
      />

      <Divider />

      <H2>PCMRITMS brain — mild cycles (A/B)</H2>
      <Callout tone="neutral" title="Mild mixed / highway: pass-through">
        Brain ON == OFF on 1200 s mixed and highway. Fuel 4.57 / 5.08 L/100km,
        assist/precharge/surge events = 0. Benefit-seeking correctly idle when
        there is no residual spike.
      </Callout>
      <Table
        headers={["Cycle", "Fuel OFF", "Fuel ON", "Δ", "Carts", "Events a/p/s"]}
        rows={[
          ["mixed", "4.57", "4.57", "0.00%", "3.8", "0/0/0"],
          ["highway", "5.08", "5.08", "0.00%", "3.3", "0/0/0"],
        ]}
        columnAlign={["left", "right", "right", "right", "right", "right"]}
      />

      <H2>PCMRITMS brain — stress (energy-normalized)</H2>
      <Text tone="secondary" size="small">
        Positive fuelEqBuf Δ = brain better after restoring buffer SoC to start.
        Raw fuel alone misleads when buffer is spent intentionally.
      </Text>
      <BarChart
        title="Brain benefit vs OFF (fuelEqBuf Δ %)"
        subtitle="Source: PCMRITMS-BRAIN-STRESS-AB · 600 s · simulation"
        categories={["transient", "tow_grade", "low_buf_launch", "n1_transient"]}
        series={[
          {
            name: "fuelEqBuf Δ % (ON better when +)",
            data: [8.74, 0.0, 8.3, 9.63],
            tone: "success",
          },
        ]}
        height={220}
      />
      <Table
        headers={[
          "Case",
          "raw Δ%",
          "fuelEqBuf Δ%",
          "a/s/p",
          "R/A",
          "cover",
          "short",
        ]}
        rows={[
          ["transient", "−5.08%", "+8.74%", "23/11/6", "0.07", "100%", "0/0"],
          ["tow_grade", "0.00%", "0.00%", "0/0/0", "0.00", "100%", "0/0"],
          ["low_buf_launch", "−3.81%", "+8.30%", "14/7/5", "0.10", "100%", "0/0"],
          ["n1_transient", "−4.14%", "+9.63%", "16/7/0", "0.00", "100%", "0/0"],
        ]}
        columnAlign={[
          "left",
          "right",
          "right",
          "right",
          "right",
          "right",
          "right",
        ]}
        rowTone={[undefined, "success", undefined, undefined]}
      />
      <Callout tone="success" title="Stress read">
        Launch / N−1: fuelEqBuf +8–10%, cover 100%, carts down ~0.5, R/A
        0.07–0.10. Tow/grade: exact pass-through (no opportunistic precharge).
        Raw fuel looks slightly worse because the buffer is being spent —
        equalized fuel shows the real win.
      </Callout>

      <Divider />

      <H2>Gate-6 add-ons in this pack</H2>
      <Grid columns={3} gap={12}>
        <Card>
          <CardHeader trailing={<Pill tone="success">PASS</Pill>}>
            ATPE-BRAIN-VV-001
          </CardHeader>
          <CardBody>
            <Text>
              HIL stub: latency 20 ms, index closure, watchdog hold, 80 ms
              reject. OVERALL=PASS.
            </Text>
          </CardBody>
        </Card>
        <Card>
          <CardHeader trailing={<Pill tone="success">OK</Pill>}>
            Fleet learning
          </CardHeader>
          <CardBody>
            <Text>
              Thermal weight 0.200 → 0.255 after stress observations (weights
              only).
            </Text>
          </CardBody>
        </Card>
        <Card>
          <CardHeader trailing={<Pill tone="warning">smoke</Pill>}>
            Endurance 1 h target
          </CardHeader>
          <CardBody>
            <Text>
              3.46 L/100km · health 95 · coolant peak 407 K · R/A 0. SoC report
              stayed 0 (empty after CS legs) — soak claims need longer
              instrumented runs.
            </Text>
          </CardBody>
        </Card>
      </Grid>

      <Divider />

      <H2>Pack step timings</H2>
      <BarChart
        title="Wall time per pack step (seconds)"
        subtitle="Source: full_pack_20260714_083750.log"
        categories={[
          "evidence",
          "gate1",
          "gate4",
          "brain A/B",
          "stress",
          "VV-001",
          "fleet",
          "endurance",
          "verify",
        ]}
        series={[
          {
            name: "elapsed_s",
            data: [62.8, 1.8, 58.2, 90.7, 188.1, 2.1, 2.1, 46.3, 87.2],
          },
        ]}
        height={220}
      />

      <Text tone="secondary" size="small">
        Artifacts: docs/evidence-pack/* · log:
        docs/evidence-pack/_runlogs/full_pack_20260714_083750.log · runner:
        scripts/run_full_digital_twin_pack.py
      </Text>
    </Stack>
  );
}
