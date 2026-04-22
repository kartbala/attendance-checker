import { useState, useEffect, useMemo } from 'react';

interface Session {
  date: string;
  scan_count: number;
  present: number;
  excused: number;
  absent: number;
  first_scan_time: string | null;
  last_scan_time: string | null;
  first_scan_minutes: number | null;
  last_scan_minutes: number | null;
}

interface HistogramBucket {
  bucket_min: number;
  count: number;
}

interface DistributionBucket {
  label: string;
  low: number;
  high: number;
  count: number;
}

interface DashboardData {
  course_code: string;
  course_name: string;
  enrolled: number;
  class_start_minutes: number;
  total_sessions: number;
  overall_attendance_rate: number;
  excluded_dates: string[];
  sessions: Session[];
  lateness_histogram: HistogramBucket[];
  attendance_distribution: DistributionBucket[];
  registered_students: number;
  unregistered_students: number;
}

interface DashboardProps {
  apiUrl: string;
  onBack: () => void;
}

const COURSES = [
  { code: 'INFO-335-04', label: 'POM (INFO 335-04)' },
  { code: 'INFO-311-05', label: 'QBA (INFO 311-05)' },
];

function fmtDate(iso: string) {
  return new Date(iso + 'T12:00:00').toLocaleDateString('en-US', {
    weekday: 'short', month: 'short', day: 'numeric',
  });
}

function fmtTimeShort(iso: string | null) {
  if (!iso) return '--';
  const m = iso.match(/(\d+):(\d+):(\d+)\s+(AM|PM)/);
  if (!m) return iso;
  return `${parseInt(m[1])}:${m[2]} ${m[4]}`;
}

function fmtMinutesLabel(m: number) {
  const hr = Math.floor(m / 60);
  const mn = Math.floor(m % 60);
  const ampm = hr >= 12 ? 'PM' : 'AM';
  const h12 = hr % 12 || 12;
  return `${h12}:${String(mn).padStart(2, '0')} ${ampm}`;
}

function SessionBars({ sessions, enrolled }: { sessions: Session[]; enrolled: number }) {
  if (!sessions.length) return null;
  const W = 720, H = 260, PAD_L = 40, PAD_R = 16, PAD_T = 20, PAD_B = 48;
  const plotW = W - PAD_L - PAD_R;
  const plotH = H - PAD_T - PAD_B;
  const barW = Math.max(6, (plotW / sessions.length) - 4);
  const step = plotW / sessions.length;

  const yFor = (v: number) => PAD_T + plotH - (v / enrolled) * plotH;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: 'auto' }}>
      {/* y-axis labels at 0, 25%, 50%, 75%, 100% */}
      {[0, 0.25, 0.5, 0.75, 1].map((frac) => {
        const v = Math.round(enrolled * frac);
        const y = yFor(v);
        return (
          <g key={frac}>
            <line x1={PAD_L} y1={y} x2={W - PAD_R} y2={y} stroke="#e5e7eb" strokeWidth="1" />
            <text x={PAD_L - 6} y={y + 4} fontSize="10" fill="#6b7280" textAnchor="end">{v}</text>
          </g>
        );
      })}
      {sessions.map((s, i) => {
        const x = PAD_L + i * step + 2;
        const pH = (s.present / enrolled) * plotH;
        const eH = (s.excused / enrolled) * plotH;
        const aH = (s.absent / enrolled) * plotH;
        const yPresent = PAD_T + plotH - pH;
        const yExcused = yPresent - eH;
        const yAbsent = yExcused - aH;
        return (
          <g key={s.date}>
            <title>{fmtDate(s.date)}: {s.present} present, {s.excused} excused, {s.absent} absent</title>
            <rect x={x} y={yPresent} width={barW} height={pH} fill="#22c55e" />
            <rect x={x} y={yExcused} width={barW} height={eH} fill="#facc15" />
            <rect x={x} y={yAbsent} width={barW} height={aH} fill="#ef4444" />
            {i % Math.max(1, Math.floor(sessions.length / 10)) === 0 && (
              <text x={x + barW / 2} y={H - PAD_B + 14} fontSize="9" fill="#6b7280"
                    textAnchor="end" transform={`rotate(-45 ${x + barW / 2} ${H - PAD_B + 14})`}>
                {fmtDate(s.date).replace(/^\w+ /, '')}
              </text>
            )}
          </g>
        );
      })}
      {/* Legend */}
      <g transform={`translate(${PAD_L}, ${H - 14})`}>
        <rect x={0} y={-10} width={12} height={12} fill="#22c55e" /><text x={16} y={0} fontSize="11" fill="#374151">Present</text>
        <rect x={80} y={-10} width={12} height={12} fill="#facc15" /><text x={96} y={0} fontSize="11" fill="#374151">Excused</text>
        <rect x={170} y={-10} width={12} height={12} fill="#ef4444" /><text x={186} y={0} fontSize="11" fill="#374151">Absent</text>
      </g>
    </svg>
  );
}

function RateLine({ sessions, enrolled }: { sessions: Session[]; enrolled: number }) {
  if (sessions.length < 2) return null;
  const W = 720, H = 200, PAD_L = 40, PAD_R = 16, PAD_T = 14, PAD_B = 36;
  const plotW = W - PAD_L - PAD_R;
  const plotH = H - PAD_T - PAD_B;
  const step = plotW / Math.max(1, sessions.length - 1);
  const rates = sessions.map((s) => (s.present + s.excused) / enrolled);
  const yFor = (r: number) => PAD_T + plotH - r * plotH;
  const points = sessions.map((s, i) => ({ x: PAD_L + i * step, y: yFor(rates[i]), s, r: rates[i] }));
  const path = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ');

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: 'auto' }}>
      {[0, 0.5, 0.75, 0.9, 1].map((r) => (
        <g key={r}>
          <line x1={PAD_L} y1={yFor(r)} x2={W - PAD_R} y2={yFor(r)}
                stroke={r === 0.9 ? '#16a34a' : '#e5e7eb'}
                strokeWidth="1" strokeDasharray={r === 0.9 ? '4 3' : undefined} />
          <text x={PAD_L - 6} y={yFor(r) + 4} fontSize="10"
                fill={r === 0.9 ? '#16a34a' : '#6b7280'} textAnchor="end">
            {Math.round(r * 100)}%
          </text>
        </g>
      ))}
      <path d={path} stroke="#2563eb" strokeWidth="2" fill="none" />
      {points.map((p, i) => (
        <g key={i}>
          <circle cx={p.x} cy={p.y} r="3" fill="#2563eb" />
          <title>{fmtDate(p.s.date)}: {Math.round(p.r * 100)}% ({p.s.present}p + {p.s.excused}e / {enrolled})</title>
        </g>
      ))}
    </svg>
  );
}

function LatenessHistogram({ buckets }: { buckets: HistogramBucket[] }) {
  if (!buckets.length) return null;
  const maxCount = Math.max(...buckets.map((b) => b.count));
  const W = 720, H = 200, PAD_L = 40, PAD_R = 16, PAD_T = 14, PAD_B = 38;
  const plotW = W - PAD_L - PAD_R;
  const plotH = H - PAD_T - PAD_B;

  // Fixed range: -20 to +30 in 2-min buckets -> 26 buckets. Render all slots
  // so the x-axis is consistent.
  const slots: HistogramBucket[] = [];
  const existing = new Map(buckets.map((b) => [b.bucket_min, b.count]));
  for (let m = -20; m <= 30; m += 2) {
    slots.push({ bucket_min: m, count: existing.get(m) ?? 0 });
  }
  const barW = plotW / slots.length - 1;
  const xFor = (i: number) => PAD_L + i * (plotW / slots.length);
  const zeroIdx = slots.findIndex((b) => b.bucket_min === 0);
  const xZero = xFor(zeroIdx) + barW / 2;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: 'auto' }}>
      {/* x-axis labels every 10 min */}
      {slots.map((b, i) => {
        if (b.bucket_min % 10 !== 0) return null;
        const x = xFor(i) + barW / 2;
        const label = b.bucket_min === 0 ? 'on time'
          : b.bucket_min > 0 ? `+${b.bucket_min} min`
          : `${b.bucket_min} min`;
        return (
          <text key={i} x={x} y={H - PAD_B + 14} fontSize="10" fill="#6b7280" textAnchor="middle">
            {label}
          </text>
        );
      })}
      {/* zero line */}
      <line x1={xZero} y1={PAD_T} x2={xZero} y2={H - PAD_B}
            stroke="#16a34a" strokeWidth="1.5" strokeDasharray="4 3" />
      <text x={xZero + 4} y={PAD_T + 10} fontSize="10" fill="#16a34a" fontWeight="600">
        class starts
      </text>
      {slots.map((b, i) => {
        const h = maxCount ? (b.count / maxCount) * plotH : 0;
        const x = xFor(i);
        const y = PAD_T + plotH - h;
        const color = b.bucket_min < 0 ? '#22c55e' : b.bucket_min === 0 ? '#facc15' : '#ef4444';
        return (
          <g key={i}>
            <title>{b.bucket_min <= -20 ? '<= -20' : b.bucket_min >= 30 ? '>= +30' : b.bucket_min} min: {b.count} scans</title>
            <rect x={x} y={y} width={barW} height={h} fill={color} opacity="0.8" />
          </g>
        );
      })}
      <text x={PAD_L} y={H - 4} fontSize="10" fill="#6b7280">
        Minutes relative to class start (one scan per student per session)
      </text>
    </svg>
  );
}

function AttendanceDistributionChart({ buckets }: { buckets: DistributionBucket[] }) {
  if (!buckets.length) return null;
  const total = buckets.reduce((s, b) => s + b.count, 0);
  const max = Math.max(1, ...buckets.map((b) => b.count));
  // Color scale: green at top, amber, red at bottom.
  const colors = ['#16a34a', '#65a30d', '#ca8a04', '#f97316', '#dc2626'];
  return (
    <div className="space-y-2">
      {buckets.map((b, i) => {
        const pct = total ? Math.round((b.count / total) * 100) : 0;
        const barPct = (b.count / max) * 100;
        return (
          <div key={b.label} className="flex items-center gap-3">
            <div className="w-20 text-right text-sm font-medium text-gray-700">{b.label}</div>
            <div className="flex-1 bg-gray-100 rounded-md h-8 overflow-hidden relative">
              <div
                className="h-full rounded-md transition-all"
                style={{ width: `${barPct}%`, backgroundColor: colors[i] || '#6b7280' }}
              />
              <div className="absolute inset-0 flex items-center px-3 text-sm font-semibold text-gray-900">
                {b.count} {b.count === 1 ? 'student' : 'students'}
                {total > 0 && <span className="text-gray-600 ml-2">({pct}%)</span>}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function Dashboard({ apiUrl, onBack }: DashboardProps) {
  const [courseCode, setCourseCode] = useState(COURSES[0].code);
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setData(null);
    fetch(`${apiUrl}/dashboard/${courseCode}`)
      .then((r) => r.json().then((j) => ({ ok: r.ok, j })))
      .then(({ ok, j }) => {
        if (cancelled) return;
        if (!ok) {
          setError(j.error || 'failed to load');
        } else {
          setData(j);
        }
      })
      .catch(() => !cancelled && setError('Could not reach server.'))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, [apiUrl, courseCode]);

  const avgLateness = useMemo(() => {
    if (!data) return null;
    let sum = 0, n = 0;
    for (const b of data.lateness_histogram) {
      sum += b.bucket_min * b.count;
      n += b.count;
    }
    return n ? sum / n : null;
  }, [data]);

  return (
    <div className="w-full max-w-4xl mx-auto space-y-4">
      <button onClick={onBack} className="text-blue-600 hover:text-blue-800 text-lg font-medium">
        &larr; Back
      </button>

      <div className="bg-white rounded-2xl shadow-lg p-6">
        <h1 className="text-3xl font-bold text-gray-900 mb-1">Class attendance dashboard</h1>
        <p className="text-gray-600 mb-4">Spring 2026 &middot; public cohort stats &middot; excludes bulk-enrollment days ({data?.excluded_dates.join(', ') || '--'})</p>

        <div className="flex gap-2 flex-wrap">
          {COURSES.map((c) => (
            <button
              key={c.code}
              onClick={() => setCourseCode(c.code)}
              className={`px-4 py-2 rounded-xl text-lg font-medium transition-colors ${
                courseCode === c.code
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
              }`}
            >
              {c.label}
            </button>
          ))}
        </div>
      </div>

      {loading && (
        <div className="bg-white rounded-2xl shadow-lg p-8 text-center text-xl text-gray-600">
          Loading...
        </div>
      )}

      {error && (
        <div className="bg-red-50 border-2 border-red-300 text-red-800 px-4 py-4 rounded-xl text-lg">
          {error}
        </div>
      )}

      {data && !loading && (
        <>
          {/* Headline numbers */}
          <div className="bg-white rounded-2xl shadow-lg p-6">
            <h2 className="text-xl font-semibold text-gray-800 mb-4">{data.course_name} &mdash; headline</h2>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div>
                <p className="text-sm text-gray-500">Overall rate</p>
                <p className="text-3xl font-bold text-gray-900">{Math.round(data.overall_attendance_rate * 100)}%</p>
                <p className="text-xs text-gray-500">present + excused / (enrolled &times; sessions)</p>
              </div>
              <div>
                <p className="text-sm text-gray-500">Sessions recorded</p>
                <p className="text-3xl font-bold text-gray-900">{data.total_sessions}</p>
              </div>
              <div>
                <p className="text-sm text-gray-500">Enrolled</p>
                <p className="text-3xl font-bold text-gray-900">{data.enrolled}</p>
              </div>
              <div>
                <p className="text-sm text-gray-500">Avg arrival</p>
                <p className="text-3xl font-bold text-gray-900">
                  {avgLateness === null ? '--' :
                   avgLateness === 0 ? 'on time' :
                   avgLateness > 0 ? `+${avgLateness.toFixed(1)} min` :
                   `${avgLateness.toFixed(1)} min`}
                </p>
                <p className="text-xs text-gray-500">vs class start ({fmtMinutesLabel(data.class_start_minutes)})</p>
              </div>
            </div>
          </div>

          {/* Rate line */}
          <div className="bg-white rounded-2xl shadow-lg p-6">
            <h2 className="text-xl font-semibold text-gray-800 mb-1">Attendance rate by session</h2>
            <p className="text-sm text-gray-500 mb-3">(present + excused) / enrolled. Dashed green = 90% target.</p>
            <RateLine sessions={data.sessions} enrolled={data.enrolled} />
          </div>

          {/* Stacked bars */}
          <div className="bg-white rounded-2xl shadow-lg p-6">
            <h2 className="text-xl font-semibold text-gray-800 mb-1">Per-session breakdown</h2>
            <p className="text-sm text-gray-500 mb-3">Present / excused / absent out of {data.enrolled} enrolled.</p>
            <SessionBars sessions={data.sessions} enrolled={data.enrolled} />
          </div>

          {/* Per-student rate distribution */}
          <div className="bg-white rounded-2xl shadow-lg p-6">
            <h2 className="text-xl font-semibold text-gray-800 mb-1">Students by attendance rate</h2>
            <p className="text-sm text-gray-500 mb-4">
              How often each registered student has been in class (attended + excused).
              Counts {data.registered_students} of {data.enrolled} enrolled
              {data.unregistered_students > 0 && ` -- ${data.unregistered_students} roster rows have no barcode on file`}.
            </p>
            <AttendanceDistributionChart buckets={data.attendance_distribution} />
          </div>

          {/* Lateness histogram */}
          <div className="bg-white rounded-2xl shadow-lg p-6">
            <h2 className="text-xl font-semibold text-gray-800 mb-1">Arrival distribution</h2>
            <p className="text-sm text-gray-500 mb-3">
              First-scan time per student per session, relative to {fmtMinutesLabel(data.class_start_minutes)}.
            </p>
            <LatenessHistogram buckets={data.lateness_histogram} />
          </div>

          {/* Per-session table */}
          <div className="bg-white rounded-2xl shadow-lg p-6">
            <h2 className="text-xl font-semibold text-gray-800 mb-1">Session details</h2>
            <p className="text-sm text-gray-500 mb-3">First and last scan time per class session.</p>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-gray-600 border-b-2 border-gray-200">
                  <tr>
                    <th className="text-left py-2 px-2">Date</th>
                    <th className="text-right py-2 px-2">Present</th>
                    <th className="text-right py-2 px-2">Excused</th>
                    <th className="text-right py-2 px-2">Absent</th>
                    <th className="text-left py-2 px-2">First scan</th>
                    <th className="text-left py-2 px-2">Last scan</th>
                  </tr>
                </thead>
                <tbody>
                  {data.sessions.map((s) => (
                    <tr key={s.date} className="border-b border-gray-100 hover:bg-gray-50">
                      <td className="py-2 px-2 font-medium">{fmtDate(s.date)}</td>
                      <td className="py-2 px-2 text-right text-green-700 font-medium">{s.present}</td>
                      <td className="py-2 px-2 text-right text-yellow-700">{s.excused}</td>
                      <td className="py-2 px-2 text-right text-red-700">{s.absent}</td>
                      <td className="py-2 px-2 text-gray-700">{fmtTimeShort(s.first_scan_time)}</td>
                      <td className="py-2 px-2 text-gray-700">{fmtTimeShort(s.last_scan_time)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
