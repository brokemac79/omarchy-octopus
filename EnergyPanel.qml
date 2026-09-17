import QtQuick
import Quickshell
import qs.Commons
import qs.Ui as Ui

Ui.Panel {
  id: root
  moduleName: "community.octopus-energy"
  ipcTarget: "community.octopus-energy"
  manageIpc: true
  property var anchorItem: null
  property var hostWidget: null
  property var report: ({})
  property double clock: Date.now()
  property bool liveStale: true
  property bool priceValid: false
  property bool fetching: false
  property bool readFailed: false
  property int tab: 0
  property bool showCost: false
  readonly property color secondary: Qt.rgba(Color.foreground.r,Color.foreground.g,Color.foreground.b,0.70)
  signal refreshRequested()
  function tm(s) { return new Date(s).toLocaleTimeString(Qt.locale("en_GB"), "HH:mm") }
  function dateLabel(s) { return new Date(s).toLocaleDateString(Qt.locale("en_GB"), "ddd d MMM") }
  function age(s) { var n=Math.max(0, Math.floor((clock-Date.parse(s))/1000)); return n<60 ? n+"s ago" : Math.floor(n/60)+"m ago" }
  readonly property var usage: report.usage || null
  readonly property var chartRows: tab === 0 ? (usage ? usage.rows : []) : report.days ? (tab === 1 ? report.days.today.rows : report.days.tomorrow.rows) : []
  readonly property var priceDay: report.days ? (tab===1 ? report.days.today : report.days.tomorrow) : null
  readonly property var current: priceValid ? report.current : null
  readonly property string section: tab === 0 ? "HOUSEHOLD CONSUMPTION" : tab === 1 ? "TODAY’S AGILE PRICES" : "TOMORROW’S AGILE PRICES"

  Ui.KeyboardPanel {
    id: popup
    anchorItem: root.anchorItem
    owner: root.hostWidget || root
    bar: root.bar
    open: root.opened
    centerOnBar: false
    focusTarget: catcher
    contentWidth: fittedContentWidth(500)
    contentHeight: fittedContentHeight(body.implicitHeight)
    Ui.PanelKeyCatcher {
      id: catcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onMoveRequested: function(dx,dy) { if (dx) root.tab = (root.tab + dx + 3)%3 }
      onTextKey: function(t) { if (t === "r") root.refreshRequested(); else if (t === "c" && root.tab === 0) root.showCost = !root.showCost }
      Flickable {
        anchors.fill: parent
        contentHeight: body.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        Column {
          id: body
          width: parent.width
          spacing: 12
          Row {
            width: parent.width
            Text { width: parent.width-95; text: "🐙  OCTOPUS ENERGY"; color: Color.foreground; font.family: Style.font.family; font.pixelSize: 14; font.bold: true }
            Rectangle {
              width: 95; height: 25; radius: 5; color: refreshMouse.containsMouse ? Color.accent : "transparent"
              Text { anchors.centerIn: parent; text: root.fetching ? "Updating…" : "Refresh ↻"; color: refreshMouse.containsMouse ? Color.background : root.secondary; font.family: Style.font.family; font.pixelSize: 12 }
              MouseArea { id: refreshMouse; anchors.fill: parent; hoverEnabled: true; onClicked: root.refreshRequested() }
            }
          }
          Row {
            width: parent.width
            Column {
              width: parent.width*0.55
              spacing: 3
              Text { text: root.report.homeMini === false ? "HOME MINI NOT ENABLED" : root.liveStale ? "LIVE POWER UNAVAILABLE / STALE" : "LIVE HOUSEHOLD POWER"; color: root.liveStale ? root.secondary : "#59d4ad"; font.pixelSize: 10; font.family: Style.font.family }
              Text { text: root.report.live ? (root.report.live.watts/1000).toFixed(2)+" kW" : "— kW"; color: root.liveStale ? root.secondary : Color.foreground; font.pixelSize: 38; font.bold: true; font.family: Style.font.family }
              Text { text: root.report.live ? "Meter reading · "+root.age(root.report.live.at) : "Waiting for meter readings"; color: root.secondary; font.pixelSize: 11; font.family: Style.font.family }
            }
            Column {
              width: parent.width*0.45
              spacing: 3
              Text { text: "ELECTRICITY RIGHT NOW"; color: root.secondary; font.pixelSize: 10; font.family: Style.font.family }
              Text { text: root.current ? Number(root.current.price).toFixed(2)+"p" : "—"; color: root.current && root.current.price<0 ? "#59d4ad" : Color.accent; font.pixelSize: 38; font.bold: true; font.family: Style.font.family }
              Text { text: root.current ? "per kWh · until "+root.current.endLabel : "Current price unavailable"; color: root.secondary; font.pixelSize: 11; font.family: Style.font.family }
            }
          }
          Row {
            width: parent.width
            spacing: 6
            Repeater {
              model: ["Usage · 24h", "Today", "Tomorrow"]
              Rectangle {
                required property string modelData
                required property int index
                width: (body.width-12)/3; height: 32; radius: 5
                color: root.tab === index ? Color.accent : Qt.rgba(Color.foreground.r,Color.foreground.g,Color.foreground.b,0.07)
                Text { anchors.centerIn: parent; text: modelData; color: root.tab===index ? Color.background : Color.foreground; font.family: Style.font.family; font.pixelSize: 12 }
                MouseArea { anchors.fill: parent; onClicked: root.tab=index }
              }
            }
          }
          Text { text: root.section; color: root.secondary; font.family: Style.font.family; font.pixelSize: 10; font.letterSpacing: 1 }
          Text {
            width: parent.width
            wrapMode: Text.Wrap
            text: root.tab===0 ? (root.usage ? root.usage.label+" · completed intervals" : "Connect your Octopus account to see usage") : (root.report.days ? (root.tab===1 ? root.report.days.today.date : root.report.days.tomorrow.date)+" · VAT included"+(root.priceDay && !root.priceDay.complete ? " · "+root.priceDay.rows.length+"/"+root.priceDay.expected+" published intervals" : "") : "Loading prices")
            color: Color.foreground; font.family: Style.font.family; font.pixelSize: 12
          }
          Row {
            visible: root.tab === 0
            spacing: 6
            Repeater {
              model: ["kWh", "£"]
              Rectangle {
                required property string modelData
                required property int index
                width: 62; height: 28; radius: 5
                color: root.showCost === (index===1) ? Color.accent : Qt.rgba(Color.foreground.r,Color.foreground.g,Color.foreground.b,0.07)
                Text { anchors.centerIn: parent; text: modelData; color: root.showCost === (index===1) ? Color.background : Color.foreground; font.family: Style.font.family; font.pixelSize: 13 }
                MouseArea { anchors.fill: parent; onClicked: root.showCost = index===1 }
              }
            }
            Text { anchors.verticalCenter: parent.verticalCenter; text: root.showCost ? "Green below zero = paid to use energy" : "Half-hourly electricity usage"; color: root.secondary; font.family: Style.font.family; font.pixelSize: 10 }
          }
          Chart { width: parent.width; rows: root.chartRows; valueKey: root.tab===0 ? (root.showCost ? "cost" : "value") : "price"; unit: root.tab===0 ? (root.showCost ? "£" : "kWh") : "p/kWh"; money: root.tab===0 && root.showCost; prices: root.tab!==0; muted: root.secondary; visible: rows.length>0 }
          Text {
            width: parent.width; wrapMode: Text.Wrap; visible: !root.chartRows.length
            text: root.tab===2 ? "Tomorrow’s rates have not been published yet. They’ll appear after the next refresh." : "No readings available yet. Check connection and account settings."
            color: root.secondary; font.family: Style.font.family; font.pixelSize: 13
          }
          Text {
            visible: root.tab===0 && root.usage !== null
            width: parent.width; wrapMode: Text.Wrap
            text: root.usage ? Number(root.usage.kwh).toFixed(2)+" kWh  ·  "+(root.usage.costComplete ? "Estimated energy £"+Number(root.usage.cost).toFixed(2) : "Cost unavailable / incomplete")+(!root.usage.complete ? "  ·  "+root.usage.rows.length+"/"+root.usage.expected+" readings" : "") : ""
            color: Color.foreground; font.family: Style.font.family; font.pixelSize: 13; font.bold: true
          }
          Rectangle { width: parent.width; height: 1; color: root.secondary; opacity: 0.25 }
          Text { text: "CHEAPEST UPCOMING WINDOWS"; color: root.secondary; font.family: Style.font.family; font.pixelSize: 10; font.letterSpacing: 1 }
          Repeater {
            model: (root.report.windows || []).filter(w => Date.parse(w.start) >= root.clock)
            Row {
              required property var modelData
              width: body.width
              Text { width: 38; text: modelData.hours+"h"; color: Color.accent; font.family: Style.font.family; font.pixelSize: 12; font.bold: true }
              Text { width: body.width-135; text: modelData.label; color: Color.foreground; font.family: Style.font.family; font.pixelSize: 12 }
              Text { width: 97; horizontalAlignment: Text.AlignRight; text: Number(modelData.price).toFixed(2)+"p/kWh"; color: modelData.price<0 ? "#59d4ad" : Color.foreground; font.family: Style.font.family; font.pixelSize: 12 }
            }
          }
          Text {
            width: parent.width; wrapMode: Text.Wrap
            visible: root.readFailed || (!!root.report.errors && root.report.errors.length>0)
            text: root.readFailed ? "Refresh failed. Previous data may be stale." : (root.report.errors || []).join("\n")
            color: "#ee9c78"; font.family: Style.font.family; font.pixelSize: 11
          }
          Text {
            width: parent.width; wrapMode: Text.Wrap
            text: "GBP · UK local time · energy cost excludes standing charge\n"+(root.report.fetchedAt ? "Checked "+root.age(root.report.fetchedAt)+" · prices cached up to 15m · usage up to 5m" : "Loading Octopus data…")
            color: root.secondary; font.family: Style.font.family; font.pixelSize: 10
          }
        }
      }
    }
  }
}
