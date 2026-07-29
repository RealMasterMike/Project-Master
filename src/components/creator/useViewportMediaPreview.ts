import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type RefObject,
} from "react";

import {
  formatProjectMasterError,
  isAbortError,
} from "../../lib/projectMasterApi";
import { previewSizeMismatch } from "./mediaPreview";

const PREVIEW_ROOT_MARGIN = "160px 0px";

interface ViewportMediaPreviewOptions {
  autoLoad: boolean;
  expectedSize: number;
  loadBlob: (signal: AbortSignal) => Promise<Blob>;
  sizeMismatchMessage: string;
}

interface ViewportMediaPreview {
  cardRef: RefObject<HTMLElement | null>;
  url: string;
  loading: boolean;
  error: string;
  load: () => Promise<void>;
  release: () => void;
  reportDecodeError: (message: string) => void;
}

export function useViewportMediaPreview({
  autoLoad,
  expectedSize,
  loadBlob,
  sizeMismatchMessage,
}: ViewportMediaPreviewOptions): ViewportMediaPreview {
  const cardRef = useRef<HTMLElement | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const objectUrlRef = useRef("");
  const loadBlobRef = useRef(loadBlob);
  const autoAttemptedRef = useRef(false);
  const disposedRef = useRef(false);
  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  loadBlobRef.current = loadBlob;

  const revokeObjectUrl = useCallback(() => {
    const currentUrl = objectUrlRef.current;
    objectUrlRef.current = "";
    if (currentUrl) URL.revokeObjectURL(currentUrl);
  }, []);

  const load = useCallback(async () => {
    autoAttemptedRef.current = true;
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setLoading(true);
    setError("");

    try {
      const blob = await loadBlobRef.current(controller.signal);
      if (previewSizeMismatch(blob.size, expectedSize)) {
        throw new Error(sizeMismatchMessage);
      }
      if (controller.signal.aborted || disposedRef.current) return;

      const nextUrl = URL.createObjectURL(blob);
      if (controller.signal.aborted || disposedRef.current) {
        URL.revokeObjectURL(nextUrl);
        return;
      }
      revokeObjectUrl();
      objectUrlRef.current = nextUrl;
      setUrl(nextUrl);
    } catch (caught) {
      if (
        !controller.signal.aborted &&
        !disposedRef.current &&
        !isAbortError(caught)
      ) {
        setError(formatProjectMasterError(caught));
      }
    } finally {
      if (controllerRef.current === controller) {
        controllerRef.current = null;
        if (!controller.signal.aborted && !disposedRef.current) {
          setLoading(false);
        }
      }
    }
  }, [expectedSize, revokeObjectUrl, sizeMismatchMessage]);

  const release = useCallback(() => {
    controllerRef.current?.abort();
    controllerRef.current = null;
    revokeObjectUrl();
    setUrl("");
    setLoading(false);
    setError("");
  }, [revokeObjectUrl]);

  const reportDecodeError = useCallback(
    (message: string) => {
      controllerRef.current?.abort();
      controllerRef.current = null;
      revokeObjectUrl();
      setUrl("");
      setLoading(false);
      setError(message);
    },
    [revokeObjectUrl],
  );

  useEffect(() => {
    disposedRef.current = false;
    return () => {
      disposedRef.current = true;
      autoAttemptedRef.current = false;
      controllerRef.current?.abort();
      controllerRef.current = null;
      revokeObjectUrl();
    };
  }, [revokeObjectUrl]);

  useEffect(() => {
    const card = cardRef.current;
    if (!autoLoad || !card || autoAttemptedRef.current) return;

    const beginAutomaticLoad = () => {
      if (autoAttemptedRef.current) return;
      autoAttemptedRef.current = true;
      void load();
    };

    if (typeof IntersectionObserver === "undefined") {
      beginAutomaticLoad();
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          observer.disconnect();
          beginAutomaticLoad();
        }
      },
      {
        rootMargin: PREVIEW_ROOT_MARGIN,
        threshold: 0.01,
      },
    );
    observer.observe(card);
    return () => observer.disconnect();
  }, [autoLoad, load]);

  return {
    cardRef,
    url,
    loading,
    error,
    load,
    release,
    reportDecodeError,
  };
}
