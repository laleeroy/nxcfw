#!/bin/bash

set -o errexit
set -o xtrace

TMP_DIR="$(dirname "$0")/tmp"
BUILD_DIR="${1:-./}"
HOMEBREW_DIR="switch"
OVERLAY_DIR="switch/.overlays"

UNZIP_COMMAND=(unzip -o)

trap 'rm -rf "$TMP_DIR"' EXIT

mkdir -p "$TMP_DIR"
mkdir -p "$BUILD_DIR/$OVERLAY_DIR"
mkdir -p "$BUILD_DIR/$HOMEBREW_DIR/DBI"

get_asset_url() {
    curl -s "https://api.github.com/repos/$1/releases/latest" | jq -r "$2"
}

download() {
    curl -O -L "$1" --output-dir "$TMP_DIR"
}

fetch_asset() {
    download "$(get_asset_url "$@")"
}

8BU() {
    fetch_asset laleeroy/nxlinks '.assets[0].browser_download_url'
    "${UNZIP_COMMAND[@]}" "$TMP_DIR"/8bit-updater.zip -d "$BUILD_DIR/switch/8bit-updater"
    touch "$BUILD_DIR/switch/8bit-updater/.8bit-updater.nro.star"
}

HBMenu() {
    fetch_asset switchbrew/nx-hbmenu '.assets[0].browser_download_url'
    "${UNZIP_COMMAND[@]}" "$TMP_DIR"/nx-hbmenu_*.zip -d "$BUILD_DIR"
}

HBLoader() {
    fetch_asset switchbrew/nx-hbloader '.assets[0].browser_download_url'
    cp "$TMP_DIR"/hbl.nsp "$BUILD_DIR/atmosphere/"
}

Hekate() {
    fetch_asset CTCaer/hekate '.assets[] | select(.name | test("Nyx.*\\.zip")) | .browser_download_url'
    "${UNZIP_COMMAND[@]}" "$TMP_DIR"/hekate*.zip -d "$BUILD_DIR"
    cp "$BUILD_DIR"/hekate*.bin "$BUILD_DIR/atmosphere/reboot_payload.bin"
    mv "$BUILD_DIR"/hekate*.bin "$BUILD_DIR/payload.bin"
}

Lockpick_RCM_Pro() {
    fetch_asset sthetix/Lockpick_RCM_Pro '.assets[0].browser_download_url'
    "${UNZIP_COMMAND[@]}" "$TMP_DIR"/Lockpick_RCM_Pro*.zip -d "$BUILD_DIR"
    mv "$BUILD_DIR/bootloader/payloads/Lockpick_RCM"*.bin "$BUILD_DIR/bootloader/payloads/Lockpick_RCM.bin"
}

SaltyNX() {
    fetch_asset masagrator/SaltyNX '.assets[] | select(.name=="SaltyNX.zip") | .browser_download_url'
    "${UNZIP_COMMAND[@]}" "$TMP_DIR"/SaltyNX.zip -d "$BUILD_DIR/"
}

DBI() {
    local data
    data=$(curl -s https://api.github.com/repos/rashevskyv/DBIPatcher/releases/latest)

    download "$(echo "$data" | jq -r '.assets[] | select(.name == "DBI.nro") | .browser_download_url')"
    download "$(echo "$data" | jq -r '.assets[] | select(.name == "translation_en.bin") | .browser_download_url')"

    mv "$TMP_DIR/translation_en.bin" "$TMP_DIR/translation.bin"

    cp "$TMP_DIR/DBI.nro" "$BUILD_DIR/switch/DBI/DBI.nro"
    cp "$TMP_DIR/translation.bin" "$BUILD_DIR/switch/DBI/translation.bin"
}

HekateToolbox() {
    fetch_asset WerWolv/Hekate-Toolbox '.assets[0].browser_download_url'
    cp "$TMP_DIR"/HekateToolbox.nro "$BUILD_DIR/switch/"
}

Sphaira() {
    fetch_asset ITotalJustice/sphaira '.assets[0].browser_download_url'
    "${UNZIP_COMMAND[@]}" "$TMP_DIR"/sphaira.zip -d "$BUILD_DIR"
    cp "$BUILD_DIR/switch/sphaira/sphaira.nro" "$BUILD_DIR/hbmenu.nro"
}

ThemezerNX() {
    fetch_asset suchmememanyskill/themezer-nx '.assets[0].browser_download_url'
    cp "$TMP_DIR"/themezer-nx.nro "$BUILD_DIR/$HOMEBREW_DIR/"
}

Appstore() {
    fetch_asset fortheusers/hb-appstore '.assets[] | select(.name == "appstore.nro") | .browser_download_url'
    cp "$TMP_DIR"/appstore.nro "$BUILD_DIR/$HOMEBREW_DIR/"
}

Sys-Patch() {
    fetch_asset borntohonk/sys-patch '.assets[0].browser_download_url'
    "${UNZIP_COMMAND[@]}" "$TMP_DIR"/sys-patch*.zip -d "$BUILD_DIR/"
}

Edizon() {
    fetch_asset proferabg/Edizon-Overlay '.assets[] | select(.name == "EdiZon-Overlay.zip") | .browser_download_url'
    "${UNZIP_COMMAND[@]}" "$TMP_DIR"/EdiZon-Overlay.zip -d "$BUILD_DIR/"
}

FPSLocker() {
    fetch_asset masagrator/FPSLocker '.assets[] | select(.name == "FPSLocker.ovl") | .browser_download_url'
    cp "$TMP_DIR"/FPSLocker.ovl "$BUILD_DIR/$OVERLAY_DIR/"
}

ReverseNX() {
    fetch_asset masagrator/ReverseNX-RT '.assets[0].browser_download_url'
    cp "$TMP_DIR"/ReverseNX-RT-ovl.ovl "$BUILD_DIR/$OVERLAY_DIR/"
}

SysModules() {
    fetch_asset ppkantorski/ovl-sysmodules '.assets[0].browser_download_url'
    cp "$TMP_DIR"/ovlSysmodules.ovl "$BUILD_DIR/$OVERLAY_DIR/"
}

Sys-Clk() {
    fetch_asset retronx-team/sys-clk '.assets[0].browser_download_url'
    "${UNZIP_COMMAND[@]}" "$TMP_DIR"/sys-clk*.zip -x README.md -d "$BUILD_DIR/"
}

Emuiibo() {
    fetch_asset XorTroll/emuiibo '.assets[] | select(.name == "emuiibo.zip") | .browser_download_url'
    "${UNZIP_COMMAND[@]}" "$TMP_DIR"/emuiibo.zip -d "$TMP_DIR"
    cp -r "$TMP_DIR/SdOut/"* "$BUILD_DIR"
}

StatusMonitor() {
    fetch_asset masagrator/Status-Monitor-Overlay '.assets[] | select(.name == "Status-Monitor-Overlay.zip") | .browser_download_url'
    "${UNZIP_COMMAND[@]}" "$TMP_DIR"/Status-Monitor-Overlay.zip -d "$BUILD_DIR/"
}

Ultrahand() {
    fetch_asset ppkantorski/Ultrahand-Overlay '.assets[] | select(.name == "sdout.zip") | .browser_download_url'
    "${UNZIP_COMMAND[@]}" "$TMP_DIR"/sdout.zip -d "$BUILD_DIR/"
}

MissionControl() {
    fetch_asset ndeadly/MissionControl '.assets[0].browser_download_url'
    "${UNZIP_COMMAND[@]}" "$TMP_DIR"/MissionControl*.zip -d "$BUILD_DIR/"
}

# Bootloaders
Hekate
Lockpick_RCM_Pro

# Homebrews
HBLoader
Sphaira
ThemezerNX
SaltyNX
DBI
HekateToolbox

# Overlays
Edizon
FPSLocker
ReverseNX
SysModules
Sys-Clk
Sys-Patch
Emuiibo
StatusMonitor
Ultrahand
MissionControl
