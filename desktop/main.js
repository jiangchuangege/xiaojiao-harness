const { app, BrowserWindow } = require('electron');
function createWindow(){
  const w = new BrowserWindow({
    width: 300, height: 280, frame: false, transparent: true,
    alwaysOnTop: true, resizable: false, hasShadow: false,
    webPreferences: { backgroundColor: '#00000000' }
  });
  w.setVisibleOnAllWorkspaces(true);
  w.loadURL('http://127.0.0.1:5000/pet');
}
app.whenReady().then(createWindow);
app.on('window-all-closed', () => app.quit());
