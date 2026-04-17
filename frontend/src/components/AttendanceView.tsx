import { useState, useEffect, useMemo } from 'react';

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
  present: { bg: 'bg-green-100', text: 'text-green-800', border: 'border-green-300', label: 'Present', dot: 'bg-green-500' },
  excused: { bg: 'bg-yellow-100', text: 'text-yellow-800', border: 'border-yellow-300', label: 'Excused', dot: 'bg-yellow-400' },
  absent:  { bg: 'bg-red-100',    text: 'text-red-800',    border: 'border-red-300',    label: 'Absent',  dot: 'bg-red-500' },
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

function ClassParticipationChart({ dates, enrolled }: { dates: DateEntry[]; enrolled: number }) {
  if (!dates.length || !enrolled) return null;
  const maxCnt = Math.max(enrolled, ...dates.map((d) => d.class_scan_count));
  return (
    <div>
      <div className="flex items-end gap-1 h-24 border-b border-gray-200">
        {dates.map((d) => {
          const h = (d.class_scan_count / maxCnt) * 100;
          const s = STATUS_STYLES[d.status];
          return (
            <div key={d.date} className="flex-1 group relative flex items-end min-w-[8px]">
              <div className={`w-full ${s.dot} rounded-t opacity-80 hover:opacity-100`}
                   style={{ height: `${h}%` }} />
              <div className="pointer-events-none absolute z-10 -top-12 left-1/2 -translate-x-1/2 whitespace-nowrap bg-gray-900 text-white text-xs px-2 py-1 rounded opacity-0 group-hover:opacity-100 transition-opacity">
                {fmtDate(d.date)}: {d.class_scan_count}/{enrolled} scanned
              </div>
            </div>
          );
        })}
      </div>
      <div className="flex justify-between text-xs text-gray-500 mt-1">
        <span>{fmtDate(dates[0].date)}</span>
        <span>{fmtDate(dates[dates.length - 1].date)}</span>
      </div>
    </div>
  );
}

function ArrivalTimesChart({ dates }: { dates: DateEntry[] }) {
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
  const minT = Math.min(...times.map(t => t.minutes)) - 5;
  const maxT = Math.max(...times.map(t => t.minutes)) + 5;
  const range = maxT - minT;

  const W = 320, H = 100, PAD = 20;
  const xStep = (W - 2 * PAD) / Math.max(1, times.length - 1);
  const points = times.map((t, i) => {
    const x = PAD + i * xStep;
    const y = H - PAD - ((t.minutes - minT) / range) * (H - 2 * PAD);
    return { x, y, t };
  });
  const path = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ');

  const fmtMin = (m: number) => {
    const hr = Math.floor(m / 60);
    const mn = Math.floor(m % 60);
    const ampm = hr >= 12 ? 'PM' : 'AM';
    const h12 = hr % 12 || 12;
    return `${h12}:${String(mn).padStart(2, '0')} ${ampm}`;
  };

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-24">
      <text x={PAD} y={12} fontSize="10" fill="#6b7280">{fmtMin(maxT)}</text>
      <text x={PAD} y={H - 5} fontSize="10" fill="#6b7280">{fmtMin(minT)}</text>
      <path d={path} stroke="#2563eb" strokeWidth="2" fill="none" />
      {points.map((p, i) => (
        <g key={i}>
          <circle cx={p.x} cy={p.y} r="3.5" fill="#2563eb" />
          <title>{fmtDate(p.t.date)}: {fmtMin(p.t.minutes)}</title>
        </g>
      ))}
    </svg>
  );
}

export function AttendanceView({ email, courseCode, onCourseSelect, apiUrl, onBack }: AttendanceViewProps) {
  const [data, setData] = useState<AttendanceData | null>(null);
  const [courses, setCourses] = useState<CourseOption[] | null>(null);
  const [studentName, setStudentName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

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

  const averagePresence = useMemo(() => {
    if (!data || !data.enrolled) return 0;
    const total = data.dates.reduce((s, d) => s + d.class_scan_count, 0);
    return total / data.dates.length / data.enrolled;
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
  const avgPct = Math.round(averagePresence * 100);

  return (
    <div className="w-full max-w-3xl mx-auto space-y-6">
      <button onClick={onBack} className="text-blue-600 hover:text-blue-800 text-lg font-medium">
        &larr; Back
      </button>

      {/* Hero card */}
      <div className="bg-gradient-to-br from-blue-600 to-indigo-700 text-white rounded-3xl shadow-xl p-8">
        <p className="text-sm uppercase tracking-wider opacity-80">{data.course_code}</p>
        <h1 className="text-3xl font-bold mt-1">{data.student_name}</h1>
        <p className="text-lg opacity-90">{data.course_name}</p>
        <p className="text-sm opacity-75 mt-2">
          Enrolled: {data.enrolled} students &middot;{' '}
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
      <div className="grid md:grid-cols-2 gap-4">
        <div className="bg-white rounded-2xl shadow-lg p-6">
          <h3 className="text-lg font-semibold text-gray-800">Class participation per session</h3>
          <p className="text-sm text-gray-500 mb-3">Classmates scanned each day (avg {avgPct}% of {data.enrolled})</p>
          <ClassParticipationChart dates={data.dates} enrolled={data.enrolled} />
        </div>
        {presentDates.length >= 2 && (
          <div className="bg-white rounded-2xl shadow-lg p-6">
            <h3 className="text-lg font-semibold text-gray-800">Your arrival times</h3>
            <p className="text-sm text-gray-500 mb-3">First scan timestamp per session you attended</p>
            <ArrivalTimesChart dates={data.dates} />
          </div>
        )}
      </div>

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
                const pct = data.enrolled ? Math.round((d.class_scan_count / data.enrolled) * 100) : 0;
                return (
                  <tr key={d.date} className="border-t hover:bg-blue-50/40">
                    <td className="px-4 py-3 text-gray-800">{fmtDate(d.date)}</td>
                    <td className="px-4 py-3">
                      <span className={`inline-block px-2.5 py-1 rounded-full text-xs font-semibold ${s.bg} ${s.text} border ${s.border}`}>
                        {s.label}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-gray-700 font-mono text-xs">
                      {d.first_scan_time || <span className="text-gray-400">--</span>}
                    </td>
                    <td className="px-4 py-3 text-gray-700">
                      <span className="font-semibold">{d.class_scan_count}</span>
                      <span className="text-gray-500">/{data.enrolled}</span>
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
    </div>
  );
}
