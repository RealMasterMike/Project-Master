import { useCallback, useEffect, useRef, useState } from "react";

import {
  getVoiceArtifactContent,
  getVoiceOverview,
  speakMessage,
  type VoiceProfileSummary,
} from "../lib/projectMasterApi";

export const SPEECH_SPEEDS = [0.75, 1, 1.25, 1.5, 2] as const;
export const SPEECH_SKIP_SECONDS = 5;

export interface MessageSpeech {
  /** Voices available to chat, cloned (reference) voices first. */
  voices: VoiceProfileSummary[];
  voiceId: string;
  setVoiceId: (voiceId: string) => void;
  autoSpeak: boolean;
  setAutoSpeak: (autoSpeak: boolean) => void;
  speed: number;
  setSpeed: (speed: number) => void;
  /** Id of the message currently playing, if any. */
  speakingId: string | null;
  pendingId: string | null;
  error: string | null;
  speak: (messageId: string, text: string) => Promise<void>;
  /** Seek by a signed number of seconds across the whole message. */
  seekBy: (seconds: number) => void;
  stop: () => void;
  available: boolean;
}

interface PlaybackQueue {
  urls: string[];
  index: number;
}

type PitchPreservingAudio = HTMLAudioElement & {
  webkitPreservesPitch?: boolean;
};

function configurePlayback(audio: HTMLAudioElement, speed: number): void {
  audio.playbackRate = speed;
  audio.preservesPitch = true;
  (audio as PitchPreservingAudio).webkitPreservesPitch = true;
}

/**
 * Speech for individual chat messages.
 *
 * A long message renders as several chunks, so playback walks a queue rather
 * than a single clip. Seeking spills across chunk boundaries so the controls
 * behave as if the message were one continuous track.
 */
export function useMessageSpeech(enabled: boolean): MessageSpeech {
  const [voices, setVoices] = useState<VoiceProfileSummary[]>([]);
  const [voiceId, setVoiceId] = useState("");
  const [autoSpeak, setAutoSpeak] = useState(false);
  const [speed, setSpeedState] = useState(1);
  const [speakingId, setSpeakingId] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlCacheRef = useRef(new Map<string, string[]>());
  const requestRef = useRef<AbortController | null>(null);
  const queueRef = useRef<PlaybackQueue | null>(null);
  const speedRef = useRef(1);

  // Gated on the caller reporting a ready backend: the desktop session token
  // is only set once the managed backend has started, so loading voices on
  // mount raced ahead of it and failed permanently.
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    void (async () => {
      try {
        const overview = await getVoiceOverview();
        if (cancelled) return;
        // Cloned voices first: chat is where cloning should be visible.
        const usable = overview.profiles
          .filter((profile) => profile.enabled)
          .sort((left, right) => {
            const leftCloned = left.mode === "reference" ? 0 : 1;
            const rightCloned = right.mode === "reference" ? 0 : 1;
            return leftCloned - rightCloned || left.name.localeCompare(right.name);
          });
        setVoices(usable);
        setVoiceId((current) =>
          usable.some((profile) => profile.id === current)
            ? current
            : usable[0]?.id ?? "",
        );
      } catch {
        // Voice Studio is optional; chat stays usable without it.
        if (!cancelled) setVoices([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [enabled]);

  const setSpeed = useCallback((next: number) => {
    speedRef.current = next;
    setSpeedState(next);
    if (audioRef.current) configurePlayback(audioRef.current, next);
  }, []);

  const stop = useCallback(() => {
    requestRef.current?.abort();
    requestRef.current = null;
    queueRef.current = null;
    const audio = audioRef.current;
    if (audio) {
      audio.onended = null;
      audio.onloadedmetadata = null;
      audio.onerror = null;
      audio.pause();
      audio.currentTime = 0;
    }
    setSpeakingId(null);
    setPendingId(null);
  }, []);

  /**
   * Play one chunk. `offset` is measured from the start, or from the end when
   * `fromEnd` is set — needed when seeking backwards into a chunk whose
   * duration is only known once its metadata loads.
   */
  const playChunk = useCallback(
    (index: number, offset = 0, fromEnd = false) => {
      const queue = queueRef.current;
      const audio = audioRef.current;
      if (!queue || !audio) return;
      if (index >= queue.urls.length) {
        queueRef.current = null;
        setSpeakingId(null);
        return;
      }
      const target = Math.max(0, index);
      queue.index = target;
      audio.onended = null;
      audio.onloadedmetadata = null;
      audio.onerror = null;
      audio.src = queue.urls[target];
      configurePlayback(audio, speedRef.current);
      const begin = () => {
        const duration = Number.isFinite(audio.duration) ? audio.duration : 0;
        const position = fromEnd ? duration - offset : offset;
        audio.currentTime = Math.max(0, Math.min(position, Math.max(0, duration - 0.05)));
        audio.onended = () => playChunk(target + 1, 0);
        void audio.play().catch((caught) => {
          if (queueRef.current !== queue || queue.index !== target) return;
          queueRef.current = null;
          setSpeakingId(null);
          setError(
            caught instanceof Error
              ? `Audio playback failed: ${caught.message}`
              : "Audio playback failed.",
          );
        });
      };
      audio.onerror = () => {
        if (queueRef.current !== queue || queue.index !== target) return;
        queueRef.current = null;
        setSpeakingId(null);
        setError("The rendered audio could not be loaded.");
      };
      if (audio.readyState >= 1) begin();
      else audio.onloadedmetadata = begin;
    },
    [],
  );

  const seekBy = useCallback(
    (seconds: number) => {
      const queue = queueRef.current;
      const audio = audioRef.current;
      if (!queue || !audio) return;
      const duration = Number.isFinite(audio.duration) ? audio.duration : 0;
      const target = audio.currentTime + seconds;
      if (target < 0) {
        // Spill backwards into the previous chunk, or clamp at the start.
        if (queue.index === 0) {
          audio.currentTime = 0;
          return;
        }
        playChunk(queue.index - 1, -target, true);
        return;
      }
      if (target > duration) {
        if (queue.index + 1 >= queue.urls.length) {
          audio.currentTime = Math.max(0, duration - 0.05);
          return;
        }
        playChunk(queue.index + 1, target - duration);
        return;
      }
      audio.currentTime = target;
    },
    [playChunk],
  );

  const speak = useCallback(
    async (messageId: string, text: string) => {
      const spoken = text.trim();
      if (!spoken || !voiceId) return;
      stop();
      setError(null);
      const cacheKey = `${voiceId}:${messageId}:${spoken}`;
      const controller = new AbortController();
      requestRef.current = controller;
      try {
        let urls = urlCacheRef.current.get(cacheKey);
        if (!urls) {
          setPendingId(messageId);
          const { artifactIds } = await speakMessage(
            spoken,
            voiceId,
            controller.signal,
          );
          if (controller.signal.aborted) return;
          const blobs = await Promise.all(
            artifactIds.map((id) =>
              getVoiceArtifactContent(id, controller.signal),
            ),
          );
          if (controller.signal.aborted) return;
          urls = blobs.map((blob) => URL.createObjectURL(blob));
          urlCacheRef.current.set(cacheKey, urls);
        }
        setPendingId(null);
        const audio = audioRef.current ?? new Audio();
        audioRef.current = audio;
        queueRef.current = { urls, index: 0 };
        setSpeakingId(messageId);
        playChunk(0, 0);
      } catch (caught) {
        setPendingId(null);
        setSpeakingId(null);
        if (caught instanceof DOMException && caught.name === "AbortError") return;
        setError(caught instanceof Error ? caught.message : "Speech failed.");
      } finally {
        if (requestRef.current === controller) requestRef.current = null;
      }
    },
    [playChunk, stop, voiceId],
  );

  useEffect(
    () => () => {
      requestRef.current?.abort();
      audioRef.current?.pause();
      for (const urls of urlCacheRef.current.values()) {
        for (const url of urls) URL.revokeObjectURL(url);
      }
      urlCacheRef.current.clear();
    },
    [],
  );

  return {
    voices,
    voiceId,
    setVoiceId,
    autoSpeak,
    setAutoSpeak,
    speed,
    setSpeed,
    speakingId,
    pendingId,
    error,
    speak,
    seekBy,
    stop,
    available: voices.length > 0,
  };
}
