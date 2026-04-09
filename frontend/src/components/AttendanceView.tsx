import { useState, useEffect } from 'react';

interface DateEntry {
  date: string;
  status: 'present' | 'excused' | 'absent';
}

interface AttendanceData {
  student_name: string;
  course_code: string;
  course_name: string;
  total_sessions: number;
  sessions_attended: number;
  excused_count: number;
  unexcused_count: number;
  effective_rate: number;
  dates: DateEntry[];
}

interface AttendanceViewProps {
  email: string;
  apiUrl: string;
  onBack: () => void;
}

const STATUS_STYLES = {
  present: { bg: 'bg-green-100', text: 'text-green-800', border: 'border-green-300', label: 'Present' },
  excused: { bg: 'bg-yellow-100', text: 'text-yellow-800', border: 'border-yellow-300', label: 'Excused' },
  absent: { bg: 'bg-red-100', text: 'text-red-800', border: 'border-red-300', label: 'Absent' },
};

export function AttendanceView({ email, apiUrl, onBack }: AttendanceViewProps) {
  const [data, setData] = useState<AttendanceData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchAttendance = async () => {
      try {
        const resp = await fetch(`${apiUrl}/attendance?email=${encodeURIComponent(email)}`);
        const json = await resp.json();

        if (!resp.ok) {
          setError(json.message || json.error);
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
  }, [email, apiUrl]);

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
        <button
          onClick={onBack}
          className="text-blue-600 hover:text-blue-800 text-lg font-medium"
        >
          &larr; Back
        </button>
      </div>
    );
  }

  if (!data) return null;

  const ratePercent = Math.round(data.effective_rate * 100);

  return (
    <div className="w-full max-w-md mx-auto space-y-6">
      <button
        onClick={onBack}
        className="text-blue-600 hover:text-blue-800 text-lg font-medium"
      >
        &larr; Back
      </button>

      <div className="text-center">
        <h1 className="text-3xl font-bold text-gray-900">{data.student_name}</h1>
        <p className="mt-1 text-xl text-gray-600">
          {data.course_name} ({data.course_code})
        </p>
      </div>

      {/* Summary stats */}
      <div className="bg-white rounded-2xl shadow-lg p-6">
        <div className="text-center mb-4">
          <p className="text-6xl font-bold text-gray-900">{ratePercent}%</p>
          <p className="text-lg text-gray-500">Effective Attendance Rate</p>
        </div>
        <div className="grid grid-cols-3 gap-4 text-center">
          <div className="bg-green-50 rounded-xl p-3">
            <p className="text-2xl font-bold text-green-800">{data.sessions_attended}</p>
            <p className="text-sm text-green-600">Present</p>
          </div>
          <div className="bg-yellow-50 rounded-xl p-3">
            <p className="text-2xl font-bold text-yellow-800">{data.excused_count}</p>
            <p className="text-sm text-yellow-600">Excused</p>
          </div>
          <div className="bg-red-50 rounded-xl p-3">
            <p className="text-2xl font-bold text-red-800">{data.unexcused_count}</p>
            <p className="text-sm text-red-600">Absent</p>
          </div>
        </div>
        <p className="text-center text-base text-gray-500 mt-3">
          {data.total_sessions} class sessions total
        </p>
      </div>

      {/* Date-by-date list */}
      <div className="bg-white rounded-2xl shadow-lg overflow-hidden">
        <h2 className="text-xl font-semibold text-gray-800 px-6 py-4 border-b">
          Session History
        </h2>
        {data.dates.length === 0 ? (
          <p className="px-6 py-4 text-gray-500">No sessions recorded yet.</p>
        ) : (
          <div>
            {data.dates.map((entry) => {
              const style = STATUS_STYLES[entry.status];
              const dateStr = new Date(entry.date + 'T12:00:00').toLocaleDateString('en-US', {
                weekday: 'short',
                month: 'short',
                day: 'numeric',
              });
              return (
                <div
                  key={entry.date}
                  className="flex items-center justify-between px-6 py-3 border-b last:border-0"
                >
                  <span className="text-lg text-gray-800">{dateStr}</span>
                  <span className={`px-3 py-1 rounded-full text-sm font-semibold ${style.bg} ${style.text} border ${style.border}`}>
                    {style.label}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
