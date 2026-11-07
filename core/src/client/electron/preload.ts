import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("whackElectron", {
  togglePanel: () => ipcRenderer.invoke("whack:toggle-panel"),
  showPanel: () => ipcRenderer.invoke("whack:show-panel"),
  hidePanel: () => ipcRenderer.invoke("whack:hide-panel"),
  triggerDiagnose: (hintLevel?: string) =>
    ipcRenderer.invoke("whack:trigger-diagnose", hintLevel),
  openInVsCode: (filePath: string, line?: number) =>
    ipcRenderer.invoke("whack:open-vscode", filePath, line),
  toggleSimulator: () => ipcRenderer.invoke("whack:toggle-simulator"),
  getSimulatorStatus: () => ipcRenderer.invoke("whack:get-simulator-status"),
  onTriggerDiagnose: (callback: (hintLevel: string) => void) => {
    ipcRenderer.on("whack:do-diagnose", (_event, hintLevel: string) =>
      callback(hintLevel)
    );
  },
  onSimulatorStatus: (callback: (running: boolean) => void) => {
    ipcRenderer.on("whack:simulator-status", (_event, running: boolean) =>
      callback(running)
    );
  },
});
