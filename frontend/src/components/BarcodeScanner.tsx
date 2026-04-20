import { useState, useEffect, useRef, useCallback } from 'react';
import { Html5Qrcode, Html5QrcodeSupportedFormats } from 'html5-qrcode';
import { useUsbScanner } from '../hooks/useUsbScanner';

interface BarcodeScannerProps {
  onScan: (barcode: string) => void;
  scannerId?: string;
}

type ScanMode = 'usb' | 'camera';

export function BarcodeScanner({ onScan, scannerId = 'barcode-reader' }: BarcodeScannerProps) {
  const [scanMode, setScanMode] = useState<ScanMode>('camera');
  const [isCameraActive, setIsCameraActive] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scannerRef = useRef<Html5Qrcode | null>(null);

  const handleScan = useCallback((barcode: string) => {
    onScan(barcode.trim());
    setIsCameraActive(false);
    if (navigator.vibrate) navigator.vibrate(200);
  }, [onScan]);

  useUsbScanner({
    onScan: handleScan,
    enabled: scanMode === 'usb' && !isCameraActive,
    minLength: 3,
    maxDelay: 50,
  });

  useEffect(() => {
    if (!isCameraActive || scanMode !== 'camera') return;
    let mounted = true;
    const start = async () => {
      await new Promise(r => setTimeout(r, 100));
      if (!scannerRef.current) {
        scannerRef.current = new Html5Qrcode(scannerId, {
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
          (decodedText) => { if (mounted) handleScan(decodedText); },
          () => {},
        );
      } catch (err) {
        if (mounted) {
          setError(err instanceof Error ? err.message : "Camera failed to start");
          setIsCameraActive(false);
        }
      }
    };
    start();
    return () => {
      mounted = false;
      if (scannerRef.current?.isScanning) scannerRef.current.stop().catch(() => {});
    };
  }, [isCameraActive, scanMode, handleScan, scannerId]);

  return (
    <div className="space-y-3">
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
      {error && (
        <div className="bg-red-50 border-2 border-red-300 text-red-800 px-4 py-3 rounded-xl text-sm">
          {error}
        </div>
      )}
      {scanMode === 'camera' && (
        <>
          <div
            id={scannerId}
            className={`w-full bg-black rounded-xl overflow-hidden transition-all ${isCameraActive ? 'h-48' : 'h-0'}`}
          />
          {!isCameraActive ? (
            <button type="button" onClick={() => setIsCameraActive(true)}
              className="w-full py-3 bg-blue-600 hover:bg-blue-700 text-white font-semibold rounded-xl transition-all">
              Start Camera
            </button>
          ) : (
            <button type="button" onClick={() => setIsCameraActive(false)}
              className="w-full py-3 bg-red-500 hover:bg-red-600 text-white font-semibold rounded-xl transition-all">
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
  );
}
