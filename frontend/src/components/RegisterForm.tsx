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
  const [showPhysicalScan, setShowPhysicalScan] = useState(false);
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
                onClick={() => setBarcodeId('')}
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

        {/* Optional physical card scan */}
        {barcodeId && !showPhysicalScan && (
          <button
            type="button"
            onClick={() => setShowPhysicalScan(true)}
            className="w-full text-sm text-gray-500 hover:text-blue-600 py-2"
          >
            + Add physical Bison card (optional -- only if you've used one in class)
          </button>
        )}

        {showPhysicalScan && (
          <div>
            <label className="block text-base font-medium text-gray-700 mb-1">
              Physical Card Barcode (optional)
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
                <button
                  type="button"
                  onClick={() => setShowPhysicalScan(false)}
                  className="w-full text-sm text-gray-500 hover:text-gray-700 py-1"
                >
                  Skip
                </button>
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
          disabled={submitting || !email || !huid || !barcodeId}
          className={`w-full py-3 text-lg font-semibold rounded-xl transition-all ${
            submitting || !email || !huid || !barcodeId
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
