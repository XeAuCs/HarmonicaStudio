#Requires AutoHotkey v2.0
#SingleInstance Force
#Warn All, StdOut
; Harmonica Studio remote protocol: 1
; Generated event table. Empty template cannot play a song.
eventText := "
(
__EVENTS__
)"
events := []
for line in StrSplit(eventText, "`n", "`r") {
    if !InStr(line, ",")
        continue
    cols := StrSplit(line, ",")
    events.Push([Integer(cols[1]), cols[2], Integer(cols[3])])
}
if A_Args.Length && A_Args[1] = "--validate" {
    last := -1
    held := Map()
    for ev in events {
        if ev[1] < last
            ExitApp(2)
        last := ev[1]
        if ev[3] {
            if held.Has(ev[2])
                ExitApp(3)
            held[ev[2]] := true
        } else {
            if !held.Has(ev[2])
                ExitApp(4)
            held.Delete(ev[2])
        }
    }
    ExitApp(held.Count ? 5 : 0)
}
running := false
target := 0
nextEvent := 1
startAt := 0
heldKeys := Map()
stopFile := A_Args.Length ? A_Args[1] : ""
commandFile := A_Args.Length >= 2 ? A_Args[2] : ""
statusFile := A_Args.Length >= 3 ? A_Args[3] : ""
lastCommandId := 0
state := "ready"
stateMessage := "演奏器已就绪；切到口琴界面按 F6 或从手机开始。"
position := 0.0
duration := events.Length ? events[events.Length][1] / 1000 : 0.0
OnExit(HandleExit)
if stopFile != "" || commandFile != ""
    SetTimer(CheckControl, 25)
if statusFile != ""
    SetTimer(PublishStatus, 100)
PublishStatus()
TrayTip("F6 开始或停止；F8 退出。切换窗口会自动停止。", "口琴演奏")

F6::TogglePlay()
F8::ExitApp()

CheckControl() {
    global stopFile, commandFile, lastCommandId
    ; Exit always takes precedence, including a request made during startup.
    if stopFile != "" && FileExist(stopFile)
        ExitApp()
    if commandFile = "" || !FileExist(commandFile)
        return
    try {
        if FileGetSize(commandFile) > 4096
            return
        command := FileRead(commandFile, "UTF-8")
        ; The desktop writes exactly these two fields using atomic replacement.
        if !RegExMatch(command, '^\s*\{\s*"id"\s*:\s*(\d+)\s*,\s*"action"\s*:\s*"(play|stop)"\s*\}\s*$', &match)
            return
        requestId := Integer(match[1])
        if requestId <= lastCommandId
            return
        lastCommandId := requestId
        if match[2] = "stop"
            StopPlay("已从手机停止演奏。")
        else
            BeginPlay()
        PublishStatus()
    }
}

JsonString(value) {
    value := StrReplace(value, '\', '\\')
    value := StrReplace(value, '"', '\"')
    value := StrReplace(value, "`r", '\r')
    value := StrReplace(value, "`n", '\n')
    value := StrReplace(value, "`t", '\t')
    return '"' value '"'
}

PublishStatus(*) {
    global statusFile, lastCommandId, state, stateMessage, position, duration, running, startAt
    if statusFile = ""
        return
    current := running ? Min(duration, Max(0.0, (NowMs() - startAt) / 1000)) : position
    data := '{"request_id":' lastCommandId ',"state":' JsonString(state)
         . ',"position":' Format('{:.3f}', current) ',"duration":' Format('{:.3f}', duration)
         . ',"message":' JsonString(stateMessage) '}'
    temporary := statusFile "." ProcessExist() ".tmp"
    try {
        output := FileOpen(temporary, "w", "UTF-8-RAW")
        output.Write(data)
        output.Close()
        ; Replace in the same directory; readers see a complete old/new JSON.
        if !DllCall("MoveFileExW", "Str", temporary, "Str", statusFile, "UInt", 9, "Int")
            FileDelete(temporary)
    }
}

HandleExit(*) {
    global state, stateMessage, running, position
    running := false
    ReleaseKeys()
    state := "idle"
    stateMessage := "演奏器已退出。"
    position := 0.0
    PublishStatus()
}

NowMs() {
    static frequency := 0
    if !frequency
        DllCall("QueryPerformanceFrequency", "Int64*", &frequency)
    counter := 0
    DllCall("QueryPerformanceCounter", "Int64*", &counter)
    return counter * 1000 / frequency
}

ReleaseKeys() {
    global heldKeys
    for key, _ in heldKeys {
        try SendEvent("{" key " up}")
    }
    heldKeys.Clear()
}

StopPlay(message := "已停止演奏。", finished := false) {
    global running, state, stateMessage, position, duration
    running := false
    SetTimer(Tick, 0)
    ReleaseKeys()
    ToolTip()
    state := "ready"
    stateMessage := message
    position := finished ? duration : 0.0
    PublishStatus()
}

TogglePlay() {
    global running
    if running
        StopPlay("已按 F6 停止演奏。")
    else
        BeginPlay()
}

BeginPlay() {
    global running, target, nextEvent, startAt, events
    global state, stateMessage, position
    ; A repeated phone request never toggles a playing song off.
    if running
        return
    if !events.Length {
        StopPlay("尚未加入曲谱，请先转换 MIDI。")
        return
    }
    target := WinExist("A")
    title := ""
    try title := WinGetTitle("ahk_id " target)
    if !RegExMatch(title, "i)三角洲|Delta.?Force|口琴钢琴测试台") {
        StopPlay("请先将电脑切换到三角洲口琴界面，再开始演奏。")
        return
    }
    nextEvent := 1
    startAt := NowMs() + 3000
    running := true
    position := 0.0
    state := "countdown"
    stateMessage := "3 秒后开始演奏；F6 停止，F8 退出。"
    SetKeyDelay(-1, -1)
    SetMouseDelay(-1)
    SetTimer(Tick, 5)
    PublishStatus()
}

Tick() {
    global running, target, nextEvent, startAt, events, heldKeys
    global state, stateMessage
    if !running
        return
    if !WinActive("ahk_id " target) {
        StopPlay("电脑已切换窗口，演奏自动停止。")
        return
    }
    elapsed := NowMs() - startAt
    if elapsed < 0 {
        state := "countdown"
        stateMessage := Ceil(-elapsed/1000) " 秒后开始演奏；F6 停止，F8 退出。"
        ToolTip("口琴将在 " Ceil(-elapsed/1000) " 秒后开始；F6 停止")
        return
    }
    state := "playing"
    stateMessage := "正在游戏内演奏；F6 停止，F8 退出。"
    ToolTip()
    try {
        while running && nextEvent <= events.Length && events[nextEvent][1] <= elapsed {
            if !WinActive("ahk_id " target) {
                StopPlay("电脑已切换窗口，演奏自动停止。")
                return
            }
            currentEvent := events[nextEvent]
            ; Avoid sending a burst of stale notes after a long system stall.
            if elapsed - currentEvent[1] > 150 {
                StopPlay("系统延迟过大，演奏已停止。")
                TrayTip("系统延迟过大，演奏已停止。按 F6 可重新开始。", "口琴演奏")
                return
            }
            Critical("On")
            if currentEvent[3] {
                heldKeys[currentEvent[2]] := true
                SendEvent("{" currentEvent[2] " down}")
            } else {
                SendEvent("{" currentEvent[2] " up}")
                if heldKeys.Has(currentEvent[2])
                    heldKeys.Delete(currentEvent[2])
            }
            nextEvent += 1
            Critical("Off")
        }
        if nextEvent > events.Length
            StopPlay("演奏完成。", true)
    } catch {
        Critical("Off")
        StopPlay("输入发送失败，演奏已停止。")
    }
}
