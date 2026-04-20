import { useState, useEffect, useMemo } from 'react';
import { BarcodeScanner } from './BarcodeScanner';

interface DateEntry {
  date: string;
  status: 'present' | 'excused' | 'absent';
  class_scan_count: number;
  first_scan_time: string | null;
  absence_type?: string;
  reason?: string;
}

interface AttendanceData {
  student_name: string;
  course_code: string;
  course_name: string;
  enrolled: number;
  barcodes_registered: string[];
  total_sessions: number;
  sessions_attended: number;
  excused_count: number;
  unexcused_count: number;
  effective_rate: number;
  dates: DateEntry[];
  section_orphan_count: number;
  has_physical_barcode: boolean;
}

interface CourseOption {
  course_code: string;
  course_name: string;
}

interface AttendanceViewProps {
  email: string;
  courseCode?: string;
  onCourseSelect: (code: string) => void;
  apiUrl: string;
  onBack: () => void;
}

const STATUS_STYLES = {
  present: { bg: 'bg-green-100', text: 'text-green-800', border: 'border-green-300', label: 'Present', dot: 'bg-green-500', fill: '#22c55e' },
  excused: { bg: 'bg-yellow-100', text: 'text-yellow-800', border: 'border-yellow-300', label: 'Excused', dot: 'bg-yellow-400', fill: '#facc15' },
  absent:  { bg: 'bg-red-100',    text: 'text-red-800',    border: 'border-red-300',    label: 'Absent',  dot: 'bg-red-500',   fill: '#ef4444' },
};

// Authoritative enrollment (source: memory project_attendance_checker.md,
// pulled 2026-04-15). student table contains drops -- override to roster.
const ENROLLED_OVERRIDE: Record<string, number> = {
  'INFO-335-04': 39,
  'INFO-311-05': 40,
};

// Class start time in minutes past midnight (ET). Used to draw the
// "class started" reference line on the arrival-times chart.
const CLASS_START_MINUTES: Record<string, number> = {
  'INFO-335-04': 12 * 60 + 40, // POM: 12:40 PM
  'INFO-311-05': 14 * 60 + 10, // QBA: 2:10 PM
};

function fmtDate(iso: string) {
  return new Date(iso + 'T12:00:00').toLocaleDateString('en-US', {
    weekday: 'short', month: 'short', day: 'numeric',
  });
}

function AttendanceRing({ rate }: { rate: number }) {
  const pct = Math.round(rate * 100);
  const r = 70, c = 2 * Math.PI * r;
  const offset = c * (1 - rate);
  const color = rate >= 0.9 ? '#16a34a' : rate >= 0.75 ? '#ca8a04' : '#dc2626';
  return (
    <div className="relative inline-flex items-center justify-center">
      <svg width="180" height="180" viewBox="0 0 180 180">
        <circle cx="90" cy="90" r={r} stroke="#e5e7eb" strokeWidth="14" fill="none" />
        <circle cx="90" cy="90" r={r} stroke={color} strokeWidth="14" fill="none"
          strokeLinecap="round" strokeDasharray={c} strokeDashoffset={offset}
          transform="rotate(-90 90 90)" style={{ transition: 'stroke-dashoffset 0.8s ease-out' }} />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-5xl font-bold text-gray-900">{pct}%</span>
        <span className="text-sm text-gray-500 tracking-wide">EFFECTIVE</span>
      </div>
    </div>
  );
}

function SessionTimeline({ dates }: { dates: DateEntry[] }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {dates.map((d) => {
        const s = STATUS_STYLES[d.status];
        return (
          <div key={d.date} className="group relative">
            <div className={`h-6 w-6 rounded-md ${s.dot} shadow-sm hover:scale-125 transition-transform cursor-pointer`} />
            <div className="pointer-events-none absolute z-10 -top-10 left-1/2 -translate-x-1/2 whitespace-nowrap bg-gray-900 text-white text-xs px-2 py-1 rounded opacity-0 group-hover:opacity-100 transition-opacity">
              {fmtDate(d.date)} -- {s.label}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ArrivalTimesChart({ dates, classStart }: { dates: DateEntry[]; classStart?: number }) {
  const presentDates = dates.filter((d) => d.first_scan_time);
  if (presentDates.length < 2) return null;

  const times = presentDates.map((d) => {
    const m = d.first_scan_time!.match(/(\d+):(\d+):(\d+)\s+(AM|PM)/);
    if (!m) return null;
    let h = parseInt(m[1]);
    const min = parseInt(m[2]);
    const sec = parseInt(m[3]);
    if (m[4] === 'PM' && h !== 12) h += 12;
    if (m[4] === 'AM' && h === 12) h = 0;
    return { date: d.date, minutes: h * 60 + min + sec / 60 };
  }).filter(Boolean) as { date: string; minutes: number }[];

  if (times.length < 2) return null;
  const allY = times.map(t => t.minutes);
  if (classStart !== undefined) allY.push(classStart);
  const minT = Math.min(...allY) - 2;
  const maxT = Math.max(...allY) + 2;
  const range = Math.max(1, maxT - minT);

  const W = 640, H = 180, PAD_L = 56, PAD_R = 16, PAD_T = 14, PAD_B = 22;
  const plotW = W - PAD_L - PAD_R;
  const plotH = H - PAD_T - PAD_B;
  const xStep = plotW / Math.max(1, times.length - 1);
  const yFor = (m: number) => PAD_T + plotH - ((m - minT) / range) * plotH;

  const points = times.map((t, i) => ({ x: PAD_L + i * xStep, y: yFor(t.minutes), t }));
  const path = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ');

  const fmtMin = (m: number) => {
    const hr = Math.floor(m / 60);
    const mn = Math.floor(m % 60);
    const ampm = hr >= 12 ? 'PM' : 'AM';
    const h12 = hr % 12 || 12;
    return `${h12}:${String(mn).padStart(2, '0')} ${ampm}`;
  };

  const yStart = classStart !== undefined ? yFor(classStart) : null;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: 'auto' }}>
      <text x={PAD_L - 4} y={PAD_T + 4} fontSize="10" fill="#6b7280" textAnchor="end">{fmtMin(maxT)}</text>
      <text x={PAD_L - 4} y={H - PAD_B} fontSize="10" fill="#6b7280" textAnchor="end">{fmtMin(minT)}</text>

      {yStart !== null && classStart !== undefined && (
        <g>
          <line x1={PAD_L} y1={yStart} x2={W - PAD_R} y2={yStart}
                stroke="#dc2626" strokeWidth="1.5" strokeDasharray="5 3" />
          <text x={W - PAD_R} y={yStart - 4} fontSize="10" fill="#dc2626" textAnchor="end" fontWeight="600">
            class starts {fmtMin(classStart)}
          </text>
        </g>
      )}

      <path d={path} stroke="#2563eb" strokeWidth="2" fill="none" />
      {points.map((p, i) => {
        const late = classStart !== undefined && p.t.minutes > classStart;
        return (
          <g key={i}>
            <circle cx={p.x} cy={p.y} r="3.5" fill={late ? '#dc2626' : '#2563eb'} />
            <title>{fmtDate(p.t.date)}: {fmtMin(p.t.minutes)}{late ? ' (late)' : ''}</title>
          </g>
        );
      })}
    </svg>
  );
}

export function AttendanceView({ email, courseCode, onCourseSelect, apiUrl, onBack }: AttendanceViewProps) {
  const [data, setData] = useState<AttendanceData | null>(null);
  const [courses, setCourses] = useState<CourseOption[] | null>(null);
  const [studentName, setStudentName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showClaimModal, setShowClaimModal] = useState(false);
  const [claimResult, setClaimResult] = useState<
    { kind: 'success'; delta: number } | { kind: 'no-match'; barcode: string } | null
  >(null);

  const shouldShowBanner =
    data && !data.has_physical_barcode &&
    data.unexcused_count >= 2 &&
    data.section_orphan_count >= 1;

  const handleClaimScan = async (scannedBarcode: string) => {
    try {
      const resp = await fetch(`${apiUrl}/claim-physical-barcode`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, physical_barcode_id: scannedBarcode }),
      });
      const j = await resp.json();
      if (!resp.ok) {
        setClaimResult({ kind: 'no-match', barcode: scannedBarcode });
        return;
      }
      const delta = j.attendance_delta.absent_before - j.attendance_delta.absent_after;
      if (delta > 0) {
        setClaimResult({ kind: 'success', delta });
        // Refetch attendance to update the page
        setTimeout(() => { setShowClaimModal(false); setClaimResult(null); window.location.reload(); }, 2500);
      } else {
        setClaimResult({ kind: 'no-match', barcode: scannedBarcode });
      }
    } catch {
      setClaimResult({ kind: 'no-match', barcode: scannedBarcode });
    }
  };

  useEffect(() => {
    const fetchAttendance = async () => {
      setLoading(true);
      setData(null);
      setCourses(null);
      setError(null);
      try {
        let url = `${apiUrl}/attendance?email=${encodeURIComponent(email)}`;
        if (courseCode) url += `&course_code=${encodeURIComponent(courseCode)}`;
        const resp = await fetch(url);
        const json = await resp.json();

        if (!resp.ok) {
          setError(json.message || json.error);
          return;
        }

        if (json.multiple_courses) {
          setCourses(json.courses);
          setStudentName(json.student_name);
          return;
        }

        setData(json);
      } catch {
        setError("Could not reach server. Try again.");
      } finally {
        setLoading(false);
      }
    };

    fetchAttendance();
  }, [email, courseCode, apiUrl]);

  const enrolled = useMemo(() => {
    if (!data) return 0;
    return ENROLLED_OVERRIDE[data.course_code] ?? data.enrolled;
  }, [data]);

  const classStart = useMemo(() => {
    if (!data) return undefined;
    return CLASS_START_MINUTES[data.course_code];
  }, [data]);

  if (loading) {
    return (
      <div className="w-full max-w-md mx-auto text-center py-12">
        <p className="text-xl text-gray-600">Loading attendance...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="w-full max-w-md mx-auto space-y-4">
        <div className="bg-red-50 border-2 border-red-300 text-red-800 px-4 py-4 rounded-xl text-lg">
          {error}
        </div>
        <button onClick={onBack} className="text-blue-600 hover:text-blue-800 text-lg font-medium">
          &larr; Back
        </button>
      </div>
    );
  }

  if (courses) {
    return (
      <div className="w-full max-w-md mx-auto space-y-4">
        <button onClick={onBack} className="text-blue-600 hover:text-blue-800 text-lg font-medium">
          &larr; Back
        </button>
        <div className="text-center">
          <h1 className="text-3xl font-bold text-gray-900">{studentName}</h1>
          <p className="mt-2 text-lg text-gray-600">Which class?</p>
        </div>
        <div className="space-y-3">
          {courses.map((c) => (
            <button key={c.course_code} onClick={() => onCourseSelect(c.course_code)}
              className="w-full bg-white rounded-2xl shadow-lg p-6 text-left hover:bg-blue-50 transition-all">
              <p className="text-xl font-semibold text-gray-900">{c.course_name}</p>
              <p className="text-base text-gray-500">{c.course_code}</p>
            </button>
          ))}
        </div>
      </div>
    );
  }

  if (!data) return null;

  const presentDates = data.dates.filter((d) => d.first_scan_time);

  return (
    <div className="w-full max-w-3xl mx-auto space-y-6">
      <button onClick={onBack} className="text-blue-600 hover:text-blue-800 text-lg font-medium">
        &larr; Back
      </button>

      {shouldShowBanner && (
        <div className="bg-amber-50 border-2 border-amber-400 rounded-2xl p-5 flex items-start gap-4">
          <div className="text-3xl">🎫</div>
          <div className="flex-1">
            <h3 className="text-lg font-semibold text-amber-900">
              Did you scan your physical Bison card in class?
            </h3>
            <p className="text-base text-amber-800 mt-1">
              There {data.section_orphan_count === 1 ? 'is' : 'are'}{' '}
              <b>{data.section_orphan_count}</b> unclaimed scan
              {data.section_orphan_count === 1 ? '' : 's'} in your section.
              Scan your physical card to count yours.
            </p>
            <button
              onClick={() => { setShowClaimModal(true); setClaimResult(null); }}
              className="mt-3 px-4 py-2 bg-amber-600 hover:bg-amber-700 text-white font-semibold rounded-lg"
            >
              Scan physical card
            </button>
          </div>
        </div>
      )}

      {/* Hero card */}
      <div className="bg-gradient-to-br from-blue-600 to-indigo-700 text-white rounded-3xl shadow-xl p-8">
        <p className="text-sm uppercase tracking-wider opacity-80">{data.course_code}</p>
        <h1 className="text-3xl font-bold mt-1">{data.student_name}</h1>
        <p className="text-lg opacity-90">{data.course_name}</p>
        <p className="text-sm opacity-75 mt-2">
          Enrolled: {enrolled} students &middot;{' '}
          Registered barcode: <code className="bg-white/20 px-2 py-0.5 rounded">{data.barcodes_registered.join(', ') || 'none'}</code>
        </p>
      </div>

      {/* Stat grid */}
      <div className="grid md:grid-cols-3 gap-4">
        <div className="md:col-span-1 bg-white rounded-2xl shadow-lg p-6 flex items-center justify-center">
          <AttendanceRing rate={data.effective_rate} />
        </div>
        <div className="md:col-span-2 bg-white rounded-2xl shadow-lg p-6 grid grid-cols-3 gap-3">
          <div className="bg-green-50 rounded-xl p-4 text-center">
            <p className="text-4xl font-bold text-green-700">{data.sessions_attended}</p>
            <p className="text-sm text-green-700 mt-1">Present</p>
          </div>
          <div className="bg-yellow-50 rounded-xl p-4 text-center">
            <p className="text-4xl font-bold text-yellow-700">{data.excused_count}</p>
            <p className="text-sm text-yellow-700 mt-1">Excused</p>
          </div>
          <div className="bg-red-50 rounded-xl p-4 text-center">
            <p className="text-4xl font-bold text-red-700">{data.unexcused_count}</p>
            <p className="text-sm text-red-700 mt-1">Absent</p>
          </div>
          <div className="col-span-3 bg-gray-50 rounded-xl p-4">
            <p className="text-sm text-gray-600 mb-2 font-medium">Session timeline</p>
            <SessionTimeline dates={data.dates} />
            <p className="text-xs text-gray-500 mt-2">{data.total_sessions} total sessions</p>
          </div>
        </div>
      </div>

      {/* Visualizations */}
      {presentDates.length >= 2 && (
        <div className="bg-white rounded-2xl shadow-lg p-6">
          <h3 className="text-lg font-semibold text-gray-800">Your arrival times</h3>
          <p className="text-sm text-gray-500 mb-3">First scan timestamp per session you attended. Red dashed line = class start.</p>
          <ArrivalTimesChart dates={data.dates} classStart={classStart} />
        </div>
      )}

      {/* Full dump table */}
      <div className="bg-white rounded-2xl shadow-lg overflow-hidden">
        <div className="px-6 py-4 border-b bg-gray-50">
          <h2 className="text-xl font-semibold text-gray-800">Session-by-session record</h2>
          <p className="text-sm text-gray-600 mt-1">
            This is the official, timestamped record. Dispute by email with the specific date and reason.
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-100 text-gray-700">
              <tr>
                <th className="text-left px-4 py-3 font-semibold">Date</th>
                <th className="text-left px-4 py-3 font-semibold">Status</th>
                <th className="text-left px-4 py-3 font-semibold">Your first scan</th>
                <th className="text-left px-4 py-3 font-semibold">Classmates scanned</th>
                <th className="text-left px-4 py-3 font-semibold">Excuse</th>
              </tr>
            </thead>
            <tbody>
              {data.dates.length === 0 ? (
                <tr><td colSpan={5} className="px-4 py-6 text-gray-500">No sessions recorded yet.</td></tr>
              ) : data.dates.map((d) => {
                const s = STATUS_STYLES[d.status];
                const pct = enrolled ? Math.round((d.class_scan_count / enrolled) * 100) : 0;
                return (
                  <tr key={d.date} className="border-t hover:bg-blue-50/40">
                    <td className="px-4 py-3 text-gray-800">{fmtDate(d.date)}</td>
                    <td className="px-4 py-3">
                      <span className={`inline-block px-2.5 py-1 rounded-full text-xs font-semibold ${s.bg} ${s.text} border ${s.border}`}>
                        {s.label}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-gray-700 font-mono text-xs">
                      {d.first_scan_time
                        ? d.first_scan_time.split(',').slice(1).join(',').trim() || d.first_scan_time
                        : <span className="text-gray-400">--</span>}
                    </td>
                    <td className="px-4 py-3 text-gray-700">
                      <span className="font-semibold">{d.class_scan_count}</span>
                      <span className="text-gray-500">/{enrolled}</span>
                      <span className="text-gray-400 text-xs ml-1">({pct}%)</span>
                    </td>
                    <td className="px-4 py-3 text-gray-700 text-xs">
                      {d.absence_type ? <><span className="font-semibold">{d.absence_type}</span>: {d.reason || ''}</> : <span className="text-gray-400">--</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div className="text-xs text-gray-500 text-center pb-6">
        Timestamps are captured by the classroom barcode scanner and recorded to the second in Eastern Time.
        Excused absences come from your Typeform submissions.
      </div>

      {showClaimModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
             onClick={() => { if (!claimResult || claimResult.kind === 'no-match') { setShowClaimModal(false); setClaimResult(null); } }}>
          <div className="bg-white rounded-2xl shadow-xl p-6 max-w-md w-full" onClick={e => e.stopPropagation()}>
            {!claimResult && (
              <>
                <h3 className="text-xl font-semibold text-gray-900 mb-3">Scan your physical card</h3>
                <BarcodeScanner onScan={handleClaimScan} scannerId="barcode-reader-claim" />
                <button onClick={() => setShowClaimModal(false)}
                        className="w-full mt-3 py-2 text-gray-600 hover:text-gray-800">Cancel</button>
              </>
            )}
            {claimResult?.kind === 'success' && (
              <div className="text-center">
                <div className="text-5xl mb-3">✅</div>
                <h3 className="text-xl font-semibold text-green-800 mb-2">Found {claimResult.delta} scan{claimResult.delta === 1 ? '' : 's'}!</h3>
                <p className="text-gray-600">Reloading your attendance...</p>
              </div>
            )}
            {claimResult?.kind === 'no-match' && (
              <div>
                <div className="text-5xl mb-3 text-center">⚠️</div>
                <h3 className="text-xl font-semibold text-gray-900 mb-2">No matching scans found</h3>
                <p className="text-gray-700 mb-3">
                  We couldn't find class scans matching that barcode. Email Dr. B at{' '}
                  <a href="mailto:karthik.b@Howard.edu" className="text-blue-600 underline">karthik.b@Howard.edu</a>{' '}
                  and include this barcode:
                </p>
                <code className="block bg-gray-100 px-3 py-2 rounded font-mono text-sm break-all">{claimResult.barcode}</code>
                <button onClick={() => { setShowClaimModal(false); setClaimResult(null); }}
                        className="w-full mt-4 py-2 bg-gray-200 hover:bg-gray-300 rounded-lg">Close</button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
