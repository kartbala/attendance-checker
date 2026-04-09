import { useState, useEffect, useRef, useCallback } from 'react';
import { Html5Qrcode, Html5QrcodeSupportedFormats } from 'html5-qrcode';
import { useUsbScanner } from '../hooks/useUsbScanner';

interface RegisterFormProps {
  onRegistered: (email: string) => void;
  onLookup: (email: string) => void;
  apiUrl: string;
}

type ScanMode = 'usb' | 'camera';

export function RegisterForm({ onRegistered, onLookup, apiUrl }: RegisterFormProps) {
  const [email, setEmail] = useState('');
  const [huid, setHuid] = useState('');
  const [barcodeId, setBarcodeId] = useState('');
  const [scanMode, setScanMode] = useState<ScanMode>('camera');
  const [isCameraActive, setIsCameraActive] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [lookupEmail, setLookupEmail] = useState('');
  const scannerRef = useRef<Html5Qrcode | null>(null);

  const handleBarcodeScan = useCallback((barcode: string) => {
    setBarcodeId(barcode.trim());
    setIsCameraActive(false);
    if (navigator.vibrate) navigator.vibrate(200);
  }, []);

  // USB scanner -- only active when not focused on an input
  useUsbScanner({
    onScan: handleBarcodeScan,
    enabled: scanMode === 'usb' && !isCameraActive,
    minLength: 3,
    maxDelay: 50,
  });

  // Camera scanner
  useEffect(() => {
    if (!isCameraActive || scanMode !== 'camera') return;
    let mounted = true;

    const startCamera = async () => {
      await new Promise(r => setTimeout(r, 100));

      if (!scannerRef.current) {
        scannerRef.current = new Html5Qrcode("barcode-reader", {
          verbose: false,
          formatsToSupport: [
            Html5QrcodeSupportedFormats.EAN_13,
            Html5QrcodeSupportedFormats.EAN_8,
            Html5QrcodeSupportedFormats.CODE_128,
            Html5QrcodeSupportedFormats.CODE_39,
            Html5QrcodeSupportedFormats.UPC_A,
            Html5QrcodeSupportedFormats.UPC_E,
          ],
        });
      }

      if (scannerRef.current.isScanning) return;

      try {
        await scannerRef.current.start(
          { facingMode: "environment" },
          { fps: 10, qrbox: { width: 250, height: 150 }, aspectRatio: 1.5 },
          (decodedText) => {
            if (!mounted) return;
            handleBarcodeScan(decodedText);
          },
          () => {},
        );
      } catch (err) {
        if (mounted) {
          setError(err instanceof Error ? err.message : "Camera failed to start");
          setIsCameraActive(false);
        }
      }
    };

    startCamera();

    return () => {
      mounted = false;
      if (scannerRef.current?.isScanning) {
        scannerRef.current.stop().catch(() => {});
      }
    };
  }, [isCameraActive, scanMode, handleBarcodeScan]);

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
            <div className="space-y-3">
              {/* Mode toggle */}
              <div className="flex bg-gray-100 rounded-xl p-1">
                <button
                  type="button"
                  onClick={() => { setIsCameraActive(false); setScanMode('camera'); }}
                  className={`flex-1 py-2 px-4 rounded-lg text-sm font-medium transition-all ${
                    scanMode === 'camera' ? 'bg-blue-600 text-white shadow-sm' : 'text-gray-600'
                  }`}
                >
                  Camera
                </button>
                <button
                  type="button"
                  onClick={() => { setIsCameraActive(false); setScanMode('usb'); }}
                  className={`flex-1 py-2 px-4 rounded-lg text-sm font-medium transition-all ${
                    scanMode === 'usb' ? 'bg-blue-600 text-white shadow-sm' : 'text-gray-600'
                  }`}
                >
                  USB Scanner
                </button>
              </div>

              {scanMode === 'camera' && (
                <>
                  <div
                    id="barcode-reader"
                    className={`w-full bg-black rounded-xl overflow-hidden transition-all ${isCameraActive ? 'h-48' : 'h-0'}`}
                  />
                  {!isCameraActive && (
                    <button
                      type="button"
                      onClick={() => setIsCameraActive(true)}
                      className="w-full py-3 bg-blue-600 hover:bg-blue-700 text-white font-semibold rounded-xl transition-all"
                    >
                      Start Camera
                    </button>
                  )}
                  {isCameraActive && (
                    <button
                      type="button"
                      onClick={() => setIsCameraActive(false)}
                      className="w-full py-3 bg-red-500 hover:bg-red-600 text-white font-semibold rounded-xl transition-all"
                    >
                      Stop Camera
                    </button>
                  )}
                </>
              )}

              {scanMode === 'usb' && (
                <div className="text-center py-6 bg-gray-50 rounded-xl border-2 border-dashed border-gray-300">
                  <p className="text-lg text-gray-600">Point your USB scanner at the barcode</p>
                  <p className="text-sm text-gray-400 mt-1">Listening for scanner input...</p>
                </div>
              )}
            </div>
          )}
        </div>

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
