export type PageOperationToken = Readonly<{
  id: number;
  controller: AbortController;
  signal: AbortSignal;
}>;

/**
 * Owns the single foreground operation of a page.
 *
 * Tauri invokes cannot always be cancelled after they reach the native side,
 * so page disposal first makes the token stale and only then aborts listeners.
 * Late payloads can therefore be rejected before JSON parsing or React state
 * updates run on the destination page's UI thread.
 */
export class PageOperationScope {
  private active = false;
  private sequence = 0;
  private currentToken: PageOperationToken | null = null;

  activate(): void {
    const previous = this.currentToken;
    this.currentToken = null;
    this.sequence += 1;
    this.active = true;
    previous?.controller.abort();
  }

  begin(): PageOperationToken {
    if (!this.active) {
      throw new Error("页面操作作用域尚未激活。");
    }
    const previous = this.currentToken;
    this.currentToken = null;
    previous?.controller.abort();

    const controller = new AbortController();
    const token: PageOperationToken = {
      id: ++this.sequence,
      controller,
      signal: controller.signal,
    };
    this.currentToken = token;
    return token;
  }

  isCurrent(token: PageOperationToken): boolean {
    return this.active
      && !token.signal.aborted
      && this.currentToken?.id === token.id;
  }

  parseIfCurrent<T>(
    token: PageOperationToken,
    rawPayload: string,
    parser: (rawPayload: string) => T,
  ): T | undefined {
    return this.isCurrent(token) ? parser(rawPayload) : undefined;
  }

  commit(token: PageOperationToken, update: () => void): boolean {
    if (!this.isCurrent(token)) return false;
    update();
    return true;
  }

  finish(token: PageOperationToken): boolean {
    if (!this.isCurrent(token)) return false;
    this.currentToken = null;
    return true;
  }

  dispose(): void {
    const previous = this.currentToken;
    this.active = false;
    this.currentToken = null;
    this.sequence += 1;
    previous?.controller.abort();
  }
}
