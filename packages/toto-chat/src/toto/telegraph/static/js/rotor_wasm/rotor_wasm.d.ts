/* tslint:disable */
/* eslint-disable */
export class WasmRotorSession {
  free(): void;
  constructor(room_slug: string, identity: string);
  create_group(): void;
  add_member(key_package_bytes: Uint8Array): Array<any>;
  key_package(): Uint8Array;
  join_from_welcome(welcome_bytes: Uint8Array): void;
  process_message(message_bytes: Uint8Array): Uint8Array | undefined;
  encrypt_app(plaintext: Uint8Array): Uint8Array;
  export_state(): Uint8Array;
  static import_state(state_bytes: Uint8Array): WasmRotorSession;
}

export type InitInput = RequestInfo | URL | Response | BufferSource | WebAssembly.Module;

export interface InitOutput {
  readonly memory: WebAssembly.Memory;
  readonly __wbg_wasmrotorsession_free: (a: number, b: number) => void;
  readonly wasmrotorsession_new: (a: number, b: number, c: number, d: number) => [number, number, number];
  readonly wasmrotorsession_create_group: (a: number) => [number, number];
  readonly wasmrotorsession_add_member: (a: number, b: number, c: number) => [number, number, number];
  readonly wasmrotorsession_key_package: (a: number) => [number, number, number, number];
  readonly wasmrotorsession_join_from_welcome: (a: number, b: number, c: number) => [number, number];
  readonly wasmrotorsession_process_message: (a: number, b: number, c: number) => [number, number, number, number];
  readonly wasmrotorsession_encrypt_app: (a: number, b: number, c: number) => [number, number, number, number];
  readonly wasmrotorsession_export_state: (a: number) => [number, number, number, number];
  readonly wasmrotorsession_import_state: (a: number, b: number) => [number, number, number];
  readonly __wbindgen_exn_store: (a: number) => void;
  readonly __externref_table_alloc: () => number;
  readonly __wbindgen_export_2: WebAssembly.Table;
  readonly __wbindgen_malloc: (a: number, b: number) => number;
  readonly __wbindgen_realloc: (a: number, b: number, c: number, d: number) => number;
  readonly __externref_table_dealloc: (a: number) => void;
  readonly __wbindgen_free: (a: number, b: number, c: number) => void;
  readonly __wbindgen_start: () => void;
}

export type SyncInitInput = BufferSource | WebAssembly.Module;
/**
* Instantiates the given `module`, which can either be bytes or
* a precompiled `WebAssembly.Module`.
*
* @param {{ module: SyncInitInput }} module - Passing `SyncInitInput` directly is deprecated.
*
* @returns {InitOutput}
*/
export function initSync(module: { module: SyncInitInput } | SyncInitInput): InitOutput;

/**
* If `module_or_path` is {RequestInfo} or {URL}, makes a request and
* for everything else, calls `WebAssembly.instantiate` directly.
*
* @param {{ module_or_path: InitInput | Promise<InitInput> }} module_or_path - Passing `InitInput` directly is deprecated.
*
* @returns {Promise<InitOutput>}
*/
export default function __wbg_init (module_or_path?: { module_or_path: InitInput | Promise<InitInput> } | InitInput | Promise<InitInput>): Promise<InitOutput>;
