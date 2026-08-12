"use client";

import { BarChart } from "@/components/BarChart";
import { Card, Metric, Stat, num } from "@/components/Card";
import { DeviceRanking } from "@/components/DeviceRanking";
import { ErrorNotice, Loading, useAsync } from "@/components/Async";
import { api, type Stats } from "@/lib/api";

export default function Overview() {
  const { data, error, loading } = useAsync<Stats>(() => api.stats());

  if (loading)
    return (
      <main>
        <Loading />
      </main>
    );
  if (error)
    return (
      <main>
        <ErrorNotice message={error} />
      </main>
    );
  if (!data) return null;

  const coverage = data.total_players
    ? Math.round((data.covered_players / data.total_players) * 100)
    : 0;

  return (
    <main>
      <section className="hero">
        <h1>Marvel Rivals pro mouse settings</h1>
        <p className="lead">
          DPI, sensitivity, eDPI and mice for the Ignite circuit — read
          passively out of public Twitch chat and Liquipedia, never asked for.
          Every reading is kept, so settings history stays visible.
        </p>
        <div className="stat-grid">
          <Stat
            accent
            value={num(data.covered_players)}
            label="Players with settings"
          />
          <Stat value={`${coverage}%`} label="Roster covered" />
          <Stat
            value={num(data.total_observations)}
            label="Readings recorded"
          />
          <Stat value={num(data.mouse_popularity.length)} label="Mice in use" />
          <Stat value={num(data.total_teams)} label="Teams" />
        </div>
      </section>

      <div className="cards wide">
        <div className="column">
          <Card title="eDPI" note={`${data.edpi.count} players`}>
            <div className="metric-row">
              <Metric value={num(data.edpi.median)} label="Median" />
              <Metric value={num(data.edpi.mean)} label="Mean" />
              <Metric
                value={`${num(data.edpi.low)}–${num(data.edpi.high)}`}
                label="Range"
              />
            </div>
            <BarChart data={data.edpi_distribution} longLabels />
            <p className="card-note" style={{ marginTop: "0.8rem" }}>
              DPI × in-game sensitivity, bucketed by 200.
            </p>
          </Card>

          <Card title="eDPI by role" note="median">
            <div className="tablewrap">
              <table>
                <thead>
                  <tr>
                    <th>Role</th>
                    <th className="num">Players</th>
                    <th className="num">Median eDPI</th>
                  </tr>
                </thead>
                <tbody>
                  {data.roles.map((r) => (
                    <tr key={r.role}>
                      <td>{r.role}</td>
                      <td className="num">{r.players_with_edpi}</td>
                      <td className="num">{num(r.median_edpi)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>

        <div className="column">
          <Card title="DPI" note={`${data.dpi.count} players`}>
            <div className="metric-row">
              <Metric value={num(data.dpi.median)} label="Median" />
              <Metric value={num(data.dpi.mean)} label="Mean" />
              <Metric
                value={
                  data.dpi_distribution.reduce(
                    (top, b) => (b.count > top.count ? b : top),
                    data.dpi_distribution[0] ?? { label: "—", count: 0 },
                  ).label
                }
                label="Most common"
              />
            </div>
            <BarChart data={data.dpi_distribution} />
          </Card>

          <Card title="Mouse usage" note="players per device">
            <DeviceRanking devices={data.mouse_popularity} />
          </Card>
        </div>
      </div>
    </main>
  );
}
