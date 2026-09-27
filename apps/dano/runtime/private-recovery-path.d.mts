export declare function privateDirectory(path: string): Promise<void>;
export declare function trustedDirectory(path: string): Promise<void>;
export declare function privateFile(path: string, maxBytes?: number): Promise<Buffer>;
export declare function outside(path: string, roots: string[]): void;
