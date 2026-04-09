import { useEffect, useRef, useCallback } from 'react';

interface UseScannerOptions {
  onScan: (barcode: string) => void;
  enabled?: boolean;
  minLength?: number;
  maxDelay?: number; // max ms between keystrokes
}

/**
 * Custom hook to detect USB barcode scanner input.
 * USB scanners work as "keyboard wedges" - they type the barcode
 * quickly followed by Enter. This hook detects rapid key sequences.
 */
export function useUsbScanner({
  onScan,
  enabled = true,
  minLength = 3,
  maxDelay = 50, // USB scanners type very fast, typically < 50ms between chars
}: UseScannerOptions) {
  const bufferRef = useRef<string>('');
  const lastKeyTimeRef = useRef<number>(0);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearBuffer = useCallback(() => {
    bufferRef.current = '';
  }, []);

  const processBuffer = useCallback(() => {
    const barcode = bufferRef.current.trim();
    if (barcode.length >= minLength) {
      onScan(barcode);
    }
    clearBuffer();
  }, [onScan, minLength, clearBuffer]);

  useEffect(() => {
    if (!enabled) {
      clearBuffer();
      return;
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      const now = Date.now();
      const timeSinceLastKey = now - lastKeyTimeRef.current;

      // If too much time has passed, this is likely manual typing - clear buffer
      if (timeSinceLastKey > maxDelay && bufferRef.current.length > 0) {
        clearBuffer();
      }

      lastKeyTimeRef.current = now;

      // Clear any pending timeout
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }

      // Enter key signals end of barcode
      if (event.key === 'Enter') {
        event.preventDefault();
        processBuffer();
        return;
      }

      // Tab key can also signal end on some scanners
      if (event.key === 'Tab' && bufferRef.current.length >= minLength) {
        event.preventDefault();
        processBuffer();
        return;
      }

      // Only capture printable characters
      if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
        // Don't capture if user is typing in an input field
        const target = event.target as HTMLElement;
        if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable) {
          return;
        }

        bufferRef.current += event.key;

        // Set timeout to process buffer if no Enter is received
        // (some scanners might not send Enter)
        timeoutRef.current = setTimeout(() => {
          if (bufferRef.current.length >= minLength) {
            processBuffer();
          }
        }, 100);
      }
    };

    document.addEventListener('keydown', handleKeyDown);

    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, [enabled, maxDelay, minLength, processBuffer, clearBuffer]);

  return { clearBuffer };
}
