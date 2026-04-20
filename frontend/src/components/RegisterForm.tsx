import { useState } from 'react';
import { BarcodeScanner } from './BarcodeScanner';

interface RegisterFormProps {
  onRegistered: (email: string) => void;
  onLookup: (email: string) => void;
  apiUrl: string;
}

export function RegisterForm({ onRegistered, onLookup, apiUrl }: RegisterFormProps) {
  const [email, setEmail] = useState('');
  const [huid, setHuid] = useState('');
  const [barcodeId, setBarcodeId] = useState('');
  const [physicalBarcodeId, setPhysicalBarcodeId] = useState('');
  const [skipReason, setSkipReason] = useState('');
  const [skipReasonOther, setSkipReasonOther] = useState('');

  const effectiveSkipReason = skipReason === 'other' ? skipReasonOther.trim() : skipReason;
  const physicalProvided = !!physicalBarcodeId;
  const skipProvided = !!effectiveSkipReason;

  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [lookupEmail, setLookupEmail] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);

    try {
      const resp = await fetch(`${apiUrl}/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: email.trim().toLowerCase(),
          huid: huid.trim(),
          barcode_id: barcodeId.trim(),
          physical_barcode_id: physicalBarcodeId.trim() || undefined,
          physical_barcode_skip_reason: effectiveSkipReason || undefined,
        }),
      });

      const data = await resp.json();

      if (!resp.ok) {
        setError(data.details ? data.details.join(", ") : data.error);
        return;
      }

      onRegistered(email.trim().toLowerCase());
    } catch {
      setError("Could not reach server. Try again.");
    } finally {
      setSubmitting(false);
    }
  };

  const handleLookup = (e: React.FormEvent) => {
    e.preventDefault();
    if (lookupEmail.trim()) {
      onLookup(lookupEmail.trim().toLowerCase());
    }
  };

  return (
    <div className="w-full max-w-md mx-auto space-y-6">
      <div className="text-center">
        <h1 className="text-3xl font-bold text-gray-900">Attendance Checker</h1>
        <p className="mt-2 text-lg text-gray-600">Dr. B's Classes</p>
      </div>

      {/* Registration Form */}
      <form onSubmit={handleSubmit} className="bg-white rounded-2xl shadow-lg p-6 space-y-4">
        <h2 className="text-xl font-semibold text-gray-800">Register Your Barcode</h2>

        <div>
          <label className="block text-base font-medium text-gray-700 mb-1">
            Bison Email
          </label>
          <input
            type="email"
            value={email}
            onChange={e => setEmail(e.target.value)}
            placeholder="yourname@bison.howard.edu"
            required
            className="w-full px-4 py-3 text-lg border-2 border-gray-300 rounded-xl focus:border-blue-500 focus:outline-none"
          />
        </div>

        <div>
          <label className="block text-base font-medium text-gray-700 mb-1">
            Howard University ID (HUID)
          </label>
          <input
            type="text"
            value={huid}
            onChange={e => setHuid(e.target.value)}
            placeholder="@03107801"
            required
            className="w-full px-4 py-3 text-lg border-2 border-gray-300 rounded-xl focus:border-blue-500 focus:outline-none"
          />
        </div>

        {/* Barcode scan area */}
        <div>
          <label className="block text-base font-medium text-gray-700 mb-1">
            Bison Card Barcode
          </label>

          {barcodeId ? (
            <div className="flex items-center justify-between bg-green-50 border-2 border-green-300 rounded-xl px-4 py-3">
              <span className="text-lg font-mono text-green-800">{barcodeId}</span>
              <button
                type="button"
                onClick={() => { setBarcodeId(''); setSkipReason(''); setSkipReasonOther(''); }}
                className="text-green-600 hover:text-green-800 text-sm font-medium"
              >
                Rescan
              </button>
            </div>
          ) : (
            <BarcodeScanner
              scannerId="barcode-reader-virtual"
              onScan={setBarcodeId}
            />
          )}
        </div>

        {/* Physical card scan */}
        {barcodeId && (
          <div>
            <label className="block text-base font-medium text-gray-700 mb-1">
              Physical Card Barcode
            </label>
            {physicalBarcodeId ? (
              <div className="flex items-center justify-between bg-green-50 border-2 border-green-300 rounded-xl px-4 py-3">
                <span className="text-lg font-mono text-green-800">{physicalBarcodeId}</span>
                <button
                  type="button"
                  onClick={() => setPhysicalBarcodeId('')}
                  className="text-green-600 hover:text-green-800 text-sm font-medium"
                >
                  Rescan
                </button>
              </div>
            ) : (
              <div className="space-y-2">
                <p className="text-sm text-gray-500">Scan the barcode on your physical Bison card</p>
                <BarcodeScanner
                  scannerId="barcode-reader-physical"
                  onScan={setPhysicalBarcodeId}
                />
              </div>
            )}

            {!physicalBarcodeId && (
              <div className="border-t pt-3 mt-2">
                <details>
                  <summary className="cursor-pointer text-sm text-gray-600 hover:text-gray-800">
                    I can't scan my physical card
                  </summary>
                  <div className="mt-3 space-y-2 pl-2">
                    {[
                      { v: 'no-physical-card', label: "I don't have a physical card" },
                      { v: 'privacy-screen', label: 'Privacy screen blocks scanning' },
                      { v: 'forgot-today', label: 'Forgot card today' },
                      { v: 'other', label: 'Other' },
                    ].map(opt => (
                      <label key={opt.v} className="flex items-center gap-2 text-base min-h-[44px]">
                        <input
                          type="radio"
                          name="physical-barcode-skip-reason"
                          value={opt.v}
                          checked={skipReason === opt.v}
                          onChange={e => setSkipReason(e.target.value)}
                        />
                        {opt.label}
                      </label>
                    ))}
                    {skipReason === 'other' && (
                      <input
                        type="text"
                        value={skipReasonOther}
                        onChange={e => setSkipReasonOther(e.target.value)}
                        placeholder="Tell us why"
                        className="w-full px-3 py-2 text-base border-2 border-gray-300 rounded-xl"
                      />
                    )}
                  </div>
                </details>
              </div>
            )}
          </div>
        )}

        {error && (
          <div className="bg-red-50 border-2 border-red-300 text-red-800 px-4 py-3 rounded-xl text-base">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={submitting || !email || !huid || !barcodeId || (!physicalProvided && !skipProvided)}
          className={`w-full py-3 text-lg font-semibold rounded-xl transition-all ${
            submitting || !email || !huid || !barcodeId || (!physicalProvided && !skipProvided)
              ? 'bg-gray-300 text-gray-500 cursor-not-allowed'
              : 'bg-blue-600 hover:bg-blue-700 text-white'
          }`}
        >
          {submitting ? 'Registering...' : 'Register'}
        </button>
      </form>

      {/* Lookup shortcut */}
      <form onSubmit={handleLookup} className="bg-white rounded-2xl shadow-lg p-6 space-y-3">
        <h2 className="text-xl font-semibold text-gray-800">Already Registered?</h2>
        <div className="flex gap-2">
          <input
            type="email"
            value={lookupEmail}
            onChange={e => setLookupEmail(e.target.value)}
            placeholder="yourname@bison.howard.edu"
            className="flex-1 px-4 py-3 text-lg border-2 border-gray-300 rounded-xl focus:border-blue-500 focus:outline-none"
          />
          <button
            type="submit"
            disabled={!lookupEmail.trim()}
            className={`px-6 py-3 text-lg font-semibold rounded-xl transition-all ${
              !lookupEmail.trim()
                ? 'bg-gray-300 text-gray-500 cursor-not-allowed'
                : 'bg-green-600 hover:bg-green-700 text-white'
            }`}
          >
            Check
          </button>
        </div>
      </form>
    </div>
  );
}
