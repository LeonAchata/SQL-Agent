"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ChartSpec, QueryResult } from "@/lib/api";

const COLORS = [1, 2, 3, 4, 5, 6].map((i) => `var(--chart-${i})`);
const MAX_POINTS = 60;

export function ResultChart({ result, spec }: { result: QueryResult; spec: ChartSpec }) {
  const xi = result.columns.indexOf(spec.x);
  const ys = spec.y.filter((y) => result.columns.includes(y));
  const data = result.rows.slice(0, MAX_POINTS).map((row) => {
    const point: Record<string, string | number> = { [spec.x]: String(row[xi] ?? "NULL") };
    for (const y of ys) point[y] = Number(row[result.columns.indexOf(y)] ?? 0);
    return point;
  });

  const axis = { stroke: "var(--muted)", fontSize: 12, tickLine: false };
  const tooltip = {
    contentStyle: {
      background: "var(--surface)",
      border: "1px solid var(--border)",
      borderRadius: 8,
      fontSize: 12,
    },
  };

  return (
    <figure className="flex flex-col gap-2">
      {spec.title && <figcaption className="text-sm font-medium">{spec.title}</figcaption>}
      <div className="h-72 w-full">
        <ResponsiveContainer>
          {spec.type === "pie" ? (
            <PieChart>
              <Pie data={data} dataKey={ys[0]} nameKey={spec.x} outerRadius="80%" label>
                {data.map((_, i) => (
                  <Cell key={i} fill={COLORS[i % COLORS.length]} />
                ))}
              </Pie>
              <Tooltip {...tooltip} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
            </PieChart>
          ) : spec.type === "line" ? (
            <LineChart data={data} margin={{ left: 8, right: 8 }}>
              <CartesianGrid stroke="var(--border)" vertical={false} />
              <XAxis dataKey={spec.x} {...axis} />
              <YAxis {...axis} width={56} />
              <Tooltip {...tooltip} />
              {ys.length > 1 && <Legend wrapperStyle={{ fontSize: 12 }} />}
              {ys.map((y, i) => (
                <Line key={y} dataKey={y} stroke={COLORS[i]} strokeWidth={2} dot={false} />
              ))}
            </LineChart>
          ) : (
            <BarChart data={data} margin={{ left: 8, right: 8 }}>
              <CartesianGrid stroke="var(--border)" vertical={false} />
              <XAxis dataKey={spec.x} {...axis} interval="preserveStartEnd" />
              <YAxis {...axis} width={56} />
              <Tooltip {...tooltip} cursor={{ fill: "var(--surface-2)" }} />
              {ys.length > 1 && <Legend wrapperStyle={{ fontSize: 12 }} />}
              {ys.map((y, i) => (
                <Bar key={y} dataKey={y} fill={COLORS[i]} radius={[3, 3, 0, 0]} />
              ))}
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>
    </figure>
  );
}
