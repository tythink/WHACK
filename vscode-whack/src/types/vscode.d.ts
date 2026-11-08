declare module "vscode" {
  export interface Disposable {
    dispose(): any;
  }

  export class EventEmitter<T> implements Disposable {
    readonly event: Event<T>;
    fire(data: T): void;
    dispose(): void;
  }

  export type Event<T> = (
    listener: (e: T) => any,
    thisArgs?: any,
    disposables?: Disposable[]
  ) => Disposable;

  export class Uri {
    readonly scheme: string;
    readonly authority: string;
    readonly path: string;
    readonly query: string;
    readonly fragment: string;
    readonly fsPath: string;
    static file(path: string): Uri;
    static parse(value: string, strict?: boolean): Uri;
    static joinPath(base: Uri, ...pathSegments: string[]): Uri;
    with(change: {
      scheme?: string;
      authority?: string;
      path?: string;
      query?: string;
      fragment?: string;
    }): Uri;
    toString(skipEncoding?: boolean): string;
  }

  export class Position {
    readonly line: number;
    readonly character: number;
    constructor(line: number, character: number);
  }

  export class Range {
    readonly start: Position;
    readonly end: Position;
    readonly isEmpty: boolean;
    readonly isSingleLine: boolean;
    constructor(start: Position, end: Position);
    constructor(
      startLine: number,
      startCharacter: number,
      endLine: number,
      endCharacter: number
    );
    contains(positionOrRange: Position | Range): boolean;
  }

  export class Selection extends Range {
    readonly anchor: Position;
    readonly active: Position;
    constructor(anchor: Position, active: Position);
    constructor(
      anchorLine: number,
      anchorCharacter: number,
      activeLine: number,
      activeCharacter: number
    );
  }

  export class ThemeColor {
    constructor(id: string);
  }

  export class MarkdownString {
    value: string;
    isTrusted?: boolean;
    supportThemeIcons?: boolean;
    constructor(value?: string, supportThemeIcons?: boolean);
    appendMarkdown(value: string): MarkdownString;
    appendCodeblock(value: string, language?: string): MarkdownString;
  }

  export interface TextLine {
    readonly lineNumber: number;
    readonly text: string;
    readonly range: Range;
    readonly firstNonWhitespaceCharacterIndex: number;
    readonly isEmptyOrWhitespace: boolean;
  }

  export interface TextDocument {
    readonly uri: Uri;
    readonly fileName: string;
    readonly isUntitled: boolean;
    readonly languageId: string;
    readonly version: number;
    readonly isDirty: boolean;
    readonly lineCount: number;
    lineAt(line: number | Position): TextLine;
    getText(range?: Range): string;
    save(): Thenable<boolean>;
  }

  export enum ViewColumn {
    Active = -1,
    Beside = -2,
    One = 1,
    Two = 2,
    Three = 3,
  }

  export enum TextEditorRevealType {
    Default = 0,
    InCenter = 1,
    InCenterIfOutsideViewport = 2,
    AtTop = 3,
  }

  export enum OverviewRulerLane {
    Left = 1,
    Center = 2,
    Right = 4,
    Full = 7,
  }

  export enum StatusBarAlignment {
    Left = 1,
    Right = 2,
  }

  export enum DiagnosticSeverity {
    Error = 0,
    Warning = 1,
    Information = 2,
    Hint = 3,
  }

  export interface Diagnostic {
    range: Range;
    message: string;
    severity: DiagnosticSeverity;
    source?: string;
  }

  export interface TextEditorDecorationType extends Disposable {
    readonly key: string;
  }

  export interface DecorationRenderOptions {
    backgroundColor?: string | ThemeColor;
    border?: string;
    borderWidth?: string;
    borderStyle?: string;
    borderColor?: string | ThemeColor;
    borderRadius?: string;
    isWholeLine?: boolean;
    overviewRulerColor?: string | ThemeColor;
    overviewRulerLane?: OverviewRulerLane;
    gutterIconPath?: string | Uri;
    gutterIconSize?: string;
    after?: {
      contentText?: string;
      color?: string | ThemeColor;
      backgroundColor?: string | ThemeColor;
      margin?: string;
      fontStyle?: string;
      fontWeight?: string;
    };
  }

  export interface DecorationOptions {
    range: Range;
    hoverMessage?: MarkdownString | MarkdownString[] | string;
    renderOptions?: {
      after?: {
        contentText?: string;
        color?: string | ThemeColor;
        backgroundColor?: string | ThemeColor;
        margin?: string;
        fontStyle?: string;
        fontWeight?: string;
      };
    };
  }

  export interface TextEditor {
    readonly document: TextDocument;
    selection: Selection;
    selections: readonly Selection[];
    readonly viewColumn?: ViewColumn;
    setDecorations(
      decorationType: TextEditorDecorationType,
      rangesOrOptions: readonly Range[] | readonly DecorationOptions[]
    ): void;
    revealRange(range: Range, revealType?: TextEditorRevealType): void;
  }

  export interface StatusBarItem extends Disposable {
    alignment: StatusBarAlignment;
    priority?: number;
    text: string;
    tooltip?: string | MarkdownString;
    color?: string | ThemeColor;
    backgroundColor?: ThemeColor;
    command?: string | Command;
    name?: string;
    show(): void;
    hide(): void;
  }

  export interface Command {
    title: string;
    command: string;
    tooltip?: string;
    arguments?: any[];
  }

  export class CodeLens {
    range: Range;
    command?: Command;
    readonly isResolved: boolean;
    constructor(range: Range, command?: Command);
  }

  export interface CodeLensProvider<T extends CodeLens = CodeLens> {
    onDidChangeCodeLenses?: Event<void>;
    provideCodeLenses(document: TextDocument): T[] | Thenable<T[]>;
  }

  export class Hover {
    contents: MarkdownString[];
    range?: Range;
    constructor(
      contents: MarkdownString | MarkdownString[],
      range?: Range
    );
  }

  export interface HoverProvider {
    provideHover(
      document: TextDocument,
      position: Position
    ): Hover | undefined | null | Thenable<Hover | undefined | null>;
  }

  export interface TextDocumentContentProvider {
    onDidChange?: Event<Uri>;
    provideTextDocumentContent(uri: Uri): string | Thenable<string>;
  }

  export class WorkspaceEdit {
    replace(uri: Uri, range: Range, newText: string): void;
    insert(uri: Uri, position: Position, newText: string): void;
    delete(uri: Uri, range: Range): void;
  }

  export interface Webview {
    options: {
      enableScripts?: boolean;
      localResourceRoots?: readonly Uri[];
    };
    html: string;
    onDidReceiveMessage: Event<any>;
    postMessage(message: any): Thenable<boolean>;
    asWebviewUri(localResource: Uri): Uri;
    readonly cspSource: string;
  }

  export interface WebviewPanel extends Disposable {
    readonly viewType: string;
    title: string;
    readonly webview: Webview;
    readonly visible: boolean;
    readonly active: boolean;
    readonly viewColumn?: ViewColumn;
    reveal(viewColumn?: ViewColumn, preserveFocus?: boolean): void;
    onDidDispose: Event<void>;
    onDidChangeViewState: Event<{ readonly webviewPanel: WebviewPanel }>;
  }

  export interface QuickPickItem {
    label: string;
    description?: string;
    detail?: string;
    picked?: boolean;
    id?: string;
  }

  export interface WorkspaceFolder {
    readonly uri: Uri;
    readonly name: string;
    readonly index: number;
  }

  export interface Configuration {
    get<T>(section: string, defaultValue: T): T;
    get<T>(section: string): T | undefined;
  }

  export enum TextDocumentChangeReason {
    Undo = 1,
    Redo = 2,
  }

  export interface TextDocumentChangeEvent {
    readonly document: TextDocument;
    readonly contentChanges: readonly any[];
    readonly reason?: TextDocumentChangeReason;
  }

  export interface TextEditorSelectionChangeEvent {
    readonly textEditor: TextEditor;
    readonly selections: readonly Selection[];
  }

  export interface ExtensionContext {
    readonly subscriptions: { dispose(): any }[];
    readonly extensionPath: string;
    readonly extensionUri: Uri;
  }

  export namespace window {
    export let activeTextEditor: TextEditor | undefined;
    export const visibleTextEditors: readonly TextEditor[];
    export function showTextDocument(
      documentOrUri: TextDocument | Uri,
      columnOrOptions?:
        | ViewColumn
        | {
            viewColumn?: ViewColumn;
            preserveFocus?: boolean;
            preview?: boolean;
            selection?: Range;
          }
    ): Thenable<TextEditor>;
    export function createTextEditorDecorationType(
      options: DecorationRenderOptions
    ): TextEditorDecorationType;
    export function createStatusBarItem(
      alignment?: StatusBarAlignment,
      priority?: number
    ): StatusBarItem;
    export function createWebviewPanel(
      viewType: string,
      title: string,
      showOptions:
        | ViewColumn
        | { viewColumn: ViewColumn; preserveFocus?: boolean },
      options?: {
        enableScripts?: boolean;
        retainContextWhenHidden?: boolean;
        localResourceRoots?: readonly Uri[];
      }
    ): WebviewPanel;
    export function showInformationMessage<T extends string>(
      message: string,
      ...items: T[]
    ): Thenable<T | undefined>;
    export function showWarningMessage<T extends string>(
      message: string,
      ...items: T[]
    ): Thenable<T | undefined>;
    export function showQuickPick<T extends QuickPickItem>(
      items: readonly T[],
      options?: { placeHolder?: string; title?: string }
    ): Thenable<T | undefined>;
    export const onDidChangeActiveTextEditor: Event<TextEditor | undefined>;
    export const onDidChangeVisibleTextEditors: Event<readonly TextEditor[]>;
    export const onDidChangeTextEditorSelection: Event<TextEditorSelectionChangeEvent>;
    export const onDidEndTerminalShellExecution:
      | Event<{
          execution: {
            commandLine: { value: string };
            read?: () => AsyncIterable<string>;
          };
          exitCode: number | undefined;
        }>
      | undefined;
  }

  export namespace workspace {
    export const workspaceFolders: readonly WorkspaceFolder[] | undefined;
    export const textDocuments: readonly TextDocument[];
    export function openTextDocument(uriOrPath: Uri | string): Thenable<TextDocument>;
    export function applyEdit(edit: WorkspaceEdit): Thenable<boolean>;
    export function getConfiguration(section?: string): Configuration;
    export function registerTextDocumentContentProvider(
      scheme: string,
      provider: TextDocumentContentProvider
    ): Disposable;
    export function findFiles(
      include: string,
      exclude?: string | null,
      maxResults?: number
    ): Thenable<Uri[]>;
    export const onDidChangeTextDocument: Event<TextDocumentChangeEvent>;
  }

  export namespace languages {
    export function registerCodeLensProvider(
      selector: string | { scheme?: string; pattern?: string },
      provider: CodeLensProvider
    ): Disposable;
    export function registerHoverProvider(
      selector: string | { scheme?: string; pattern?: string },
      provider: HoverProvider
    ): Disposable;
    export function getDiagnostics(resource?: Uri): Diagnostic[];
    export const onDidChangeDiagnostics: Event<{ readonly uris: readonly Uri[] }>;
  }

  export namespace commands {
    export function registerCommand(
      command: string,
      callback: (...args: any[]) => any
    ): Disposable;
    export function executeCommand<T = unknown>(
      command: string,
      ...rest: any[]
    ): Thenable<T>;
  }
}
