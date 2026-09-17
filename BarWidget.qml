import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui as Ui

Ui.BarWidget {
  id: root
  moduleName: "community.octopus-energy"
  property var report: ({})
  property double clock: Date.now()
  property bool readFailed: false
  readonly property bool opened: energy.opened
  readonly property bool popoutSwitchClosing: energy.popoutSwitchClosing
  readonly property bool stale: !report.live || clock - Date.parse(report.live.at) > 120000
  readonly property bool priceValid: !!report.current && clock >= Date.parse(report.current.start) && clock < Date.parse(report.current.end)
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight
  function refresh(force) { if (!fetcher.running) { fetcher.command = ["python3", decodeURIComponent(Qt.resolvedUrl("octopus.py").toString().replace(/^file:\/\//, ""))].concat(force === true ? ["--refresh"] : []); fetcher.running = true } }
  function open() { energy.open(); refresh() }
  function close() { energy.close() }
  function toggle() { opened ? close() : open() }
  function closeForPopoutSwitch() { energy.closeForPopoutSwitch() }

  Timer { interval: 1000; running: true; repeat: true; onTriggered: root.clock = Date.now() }
  Timer { interval: root.opened ? 15000 : 60000; running: true; repeat: true; triggeredOnStart: true; onTriggered: root.refresh() }
  Process {
    id: fetcher
    command: ["python3", Qt.resolvedUrl("octopus.py").toString().replace(/^file:\/\//, "")]
    stdout: StdioCollector {
      onStreamFinished: {
        try {
          var result = JSON.parse(text)
          if (result.schema === 1) { root.report = result; root.readFailed = false }
          else root.readFailed = true
        } catch (e) { root.readFailed = true }
      }
    }
    onExited: function(code) { if (code !== 0) root.readFailed = true }
  }
  Ui.WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "🐙 " + (root.priceValid ? Number(root.report.current.price).toFixed(1) + "p" : "—")
    tooltipText: "Octopus Energy · click for live power and prices"
    onPressed: function(b) { if (b === Qt.MiddleButton) root.refresh(); else root.toggle() }
  }
  EnergyPanel {
    id: energy
    bar: root.bar
    settings: root.settings
    anchorItem: button
    hostWidget: root
    report: root.report
    clock: root.clock
    liveStale: root.stale
    priceValid: root.priceValid
    fetching: fetcher.running
    readFailed: root.readFailed
    onRefreshRequested: root.refresh(true)
  }
}
