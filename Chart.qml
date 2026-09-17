import QtQuick
import qs.Commons

Item {
  id: root
  property var rows: []
  property string valueKey: "price"
  property string unit: "p/kWh"
  property bool prices: true
  property bool money: false
  readonly property var values: rows.filter(r => r[valueKey] !== null && r[valueKey] !== undefined && isFinite(Number(r[valueKey]))).map(r => Number(r[valueKey]))
  property string timezone: "Europe/London"
  property int hoverIndex: -1
  property color accent: Color.accent
  property color foreground: Color.foreground
  property color muted: Color.muted
  readonly property real high: Math.max(money ? 0.01 : 1, ...values)
  readonly property real low: Math.min(0, ...values)
  implicitHeight: 195
  onRowsChanged: { hoverIndex = -1; plot.requestPaint() }
  onValueKeyChanged: { hoverIndex = -1; plot.requestPaint() }
  onMoneyChanged: plot.requestPaint()
  onPricesChanged: plot.requestPaint()
  onAccentChanged: plot.requestPaint()
  onForegroundChanged: plot.requestPaint()
  onHoverIndexChanged: plot.requestPaint()
  function label(s) { return new Date(s).toLocaleTimeString(Qt.locale("en_GB"), "HH:mm") }
  Text {
    anchors.top: parent.top
    color: root.foreground
    font.family: Style.font.family
    font.pixelSize: 12
    text: root.hoverIndex >= 0 && root.rows[root.hoverIndex] ? root.rows[root.hoverIndex].label + "  ·  " + (root.rows[root.hoverIndex][root.valueKey] === null ? "Cost unavailable" : root.money ? "£" + Number(root.rows[root.hoverIndex][root.valueKey]).toFixed(3) : Number(root.rows[root.hoverIndex][root.valueKey]).toFixed(2) + " " + root.unit) : "Hover a bar for its reading"
  }
  Canvas {
    id: plot
    anchors { left: parent.left; right: parent.right; top: parent.top; topMargin: 26; bottom: parent.bottom; bottomMargin: 20 }
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onPaint: {
      var ctx = getContext("2d"); ctx.reset()
      var left = 38, right = width - 4, top = 8, bottom = height - 5
      var range = root.high - root.low
      function y(v) { return bottom - (v-root.low)/range*(bottom-top) }
      ctx.font = "10px monospace"; ctx.fillStyle = root.muted
      ctx.fillText(root.high.toFixed(root.money ? 2 : root.prices ? 0 : 1), 0, top+4)
      ctx.fillText(root.low.toFixed(root.money ? 2 : root.prices ? 0 : 1), 0, bottom)
      ctx.strokeStyle = root.muted; ctx.globalAlpha = 0.35
      ctx.beginPath(); ctx.moveTo(left, y(0)); ctx.lineTo(right, y(0)); ctx.stroke(); ctx.globalAlpha = 1
      if (root.low < 0 && root.high > 0) { ctx.fillStyle=root.muted; ctx.fillText("0", 0, y(0)+3) }
      if (!root.rows.length) return
      var first = Date.parse(root.rows[0].start)
      var last = Date.parse(root.rows[root.rows.length-1].start) + 1800000
      var slots = Math.max(1, (last-first)/1800000), bw = (right-left)/slots
      root.rows.forEach(function(row, i) {
        if (row[root.valueKey] === null || row[root.valueKey] === undefined) return
        var v = Number(row[root.valueKey]), pos = (Date.parse(row.start)-first)/1800000
        ctx.fillStyle = root.money && v<0 ? "#59d4ad" : root.prices ? (v<0 ? "#59d4ad" : v>30 ? "#ee9c78" : root.accent) : root.accent
        ctx.globalAlpha = root.hoverIndex===i ? 1 : 0.78
        ctx.fillRect(left+pos*bw+1, Math.min(y(v),y(0)), Math.max(1,bw-2), Math.max(1,Math.abs(y(v)-y(0))))
      }); ctx.globalAlpha = 1
    }
    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      onPositionChanged: function(mouse) {
        if (!root.rows.length) return
        var first = Date.parse(root.rows[0].start), last=Date.parse(root.rows[root.rows.length-1].start)+1800000
        var t = first + (mouse.x-38)/(width-42)*(last-first)
        root.hoverIndex = root.rows.findIndex(r => Date.parse(r.start)<=t && t<Date.parse(r.start)+1800000)
      }
      onExited: root.hoverIndex = -1
    }
  }
  Row {
    anchors { left: parent.left; leftMargin: 38; right: parent.right; bottom: parent.bottom }
    Text { width: parent.width/2; text: root.rows.length ? root.rows[0].label : ""; color: root.muted; font.pixelSize: 11; font.family: Style.font.family }
    Text { width: parent.width/2; horizontalAlignment: Text.AlignRight; text: root.rows.length ? root.rows[root.rows.length-1].endLabel : ""; color: root.muted; font.pixelSize: 11; font.family: Style.font.family }
  }
}
