import type { JSX } from "react";
import {
  AiderMark,
  ClaudeMark,
  ClineMark,
  CodexMark,
  CodyMark,
  ContinueMark,
  CopilotMark,
  CursorMark,
  GeminiMark,
  OpencodeMark,
  OpenInterpreterMark,
  WindsurfMark,
  type VendorMarkProps,
} from "./VendorLogos";

/**
 * Vendor-keyed lookup so data-driven callers (e.g. LogoCloud's provider
 * list) can resolve a brand mark from a string key without importing each
 * component by name. Lives in a separate file from VendorLogos.tsx so the
 * component file stays a pure react-refresh boundary.
 */

export type VendorKey =
  | "claude"
  | "cursor"
  | "codex"
  | "gemini"
  | "windsurf"
  | "copilot"
  | "cline"
  | "aider"
  | "continue"
  | "opencode"
  | "cody"
  | "openinterpreter";

export const VENDOR_MARK: Record<
  VendorKey,
  (props: VendorMarkProps) => JSX.Element
> = {
  claude: ClaudeMark,
  cursor: CursorMark,
  codex: CodexMark,
  gemini: GeminiMark,
  windsurf: WindsurfMark,
  copilot: CopilotMark,
  cline: ClineMark,
  aider: AiderMark,
  continue: ContinueMark,
  opencode: OpencodeMark,
  cody: CodyMark,
  openinterpreter: OpenInterpreterMark,
};

export type { VendorMarkProps };
