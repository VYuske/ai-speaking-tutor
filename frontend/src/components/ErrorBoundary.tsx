import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
  /** Bumped on reset so the children are mounted from scratch. */
  resetKey: number;
}

/**
 * Last line of defence: a render error anywhere below would otherwise leave a
 * blank page with no way back. "Voltar ao início" remounts the app at the setup
 * screen instead of reloading, so nothing in flight is lost to a full navigation.
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, resetKey: 0 };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("erro de render na interface:", error, info.componentStack);
  }

  private reset = (): void => {
    this.setState((prev) => ({ error: null, resetKey: prev.resetKey + 1 }));
  };

  render(): ReactNode {
    const { error, resetKey } = this.state;
    if (error) {
      return (
        <div
          role="alert"
          className="flex min-h-full items-center justify-center bg-slate-950 p-6"
        >
          <div className="w-full max-w-md rounded-2xl border border-rose-500/40 bg-rose-500/10 p-6 text-center">
            <h1 className="text-lg font-semibold text-rose-100">Algo deu errado</h1>
            <p className="mt-2 text-sm text-rose-200">
              A interface encontrou um erro inesperado.
            </p>
            <p className="mt-2 break-words text-xs text-rose-300/80">{error.message}</p>
            <button
              type="button"
              onClick={this.reset}
              className="mt-4 rounded-full border border-rose-400/60 px-4 py-1.5 text-xs text-rose-100 transition hover:border-rose-300"
            >
              Voltar ao início
            </button>
          </div>
        </div>
      );
    }
    return <div key={resetKey} className="h-full">{this.props.children}</div>;
  }
}
