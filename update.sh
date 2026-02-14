#!/bin/bash

set -o errexit
set -o xtrace

TMP_DIR="$(dirname $0)/tmp"
BUILD_DIR=$(if [[ -z $1 ]]; then echo "./"; else echo $1; fi)
HOMEBREW_DIR=switch
OVERLAY_DIR=switch/.overlays

UNZIP_COMMAND="unzip -o"

8BU() {
    download_url=$(curl -s https://api.github.com/repos/laleeroy/nxlinks/releases/latest | jq -r ".assets[0].browser_download_url")
    curl -O -L $download_url --output-dir $TMP_DIR
    $UNZIP_COMMAND $TMP_DIR/8bit-updater.zip -d $BUILD_DIR/switch/8bit-updater
    touch switch/8bit-updater/.8bit-updater.nro.star
}

HBMenu() {
    download_url=$(curl -s https://api.github.com/repos/switchbrew/nx-hbmenu/releases/latest | jq -r ".assets[0].browser_download_url")
    curl -O -L $download_url --output-dir $TMP_DIR
    $UNZIP_COMMAND $TMP_DIR/nx-hbmenu_*.zip -d $BUILD_DIR
}

HBLoader() {
    download_url=$(curl -s https://api.github.com/repos/switchbrew/nx-hbloader/releases/latest | jq -r ".assets[0].browser_download_url")
    curl -O -L $download_url --output-dir $TMP_DIR
    cp $TMP_DIR/hbl.nsp -d $BUILD_DIR/atmosphere
}

Hekate() {
    download_url=$(curl -s https://api.github.com/repos/CTCaer/hekate/releases/latest | jq -r ".assets[1].browser_download_url")
    curl -O -L $download_url --output-dir $TMP_DIR
    $UNZIP_COMMAND $TMP_DIR/hekate*.zip -d $BUILD_DIR
    cp $BUILD_DIR/hekate*.bin $BUILD_DIR/atmosphere/reboot_payload.bin
    mv $BUILD_DIR/hekate*.bin $BUILD_DIR/payload.bin
}

Ovlloader() {
    download_url=$(curl -s https://api.github.com/repos/WerWolv/nx-ovlloader/releases/latest | jq -r ".assets[0].browser_download_url")
    curl -O -L $download_url --output-dir $TMP_DIR
    $UNZIP_COMMAND $TMP_DIR/nx-ovlloader.zip -d $BUILD_DIR
}

QuickNTP() {
    download_url=$(curl -s https://api.github.com/repos/nedex/QuickNTP/releases/latest | jq -r ".assets[0].browser_download_url")
    curl -O -L $download_url --output-dir $TMP_DIR
    $UNZIP_COMMAND $TMP_DIR/quickntp*.zip -d $BUILD_DIR
}

SaltyNX() {
    download_url=$(curl -s https://api.github.com/repos/masagrator/SaltyNX/releases/latest | jq -r ".assets[1].browser_download_url")
    curl -O -L $download_url --output-dir $TMP_DIR
    $UNZIP_COMMAND $TMP_DIR/SaltyNX*.zip -d $BUILD_DIR/
}

Sphaira() {
    download_url=$(curl -s https://api.github.com/repos/ITotalJustice/sphaira/releases/latest | jq -r ".assets[0].browser_download_url")
    curl -O -L $download_url --output-dir $TMP_DIR
    $UNZIP_COMMAND "$TMP_DIR/sphaira.zip" -d "$BUILD_DIR"
    cp "$BUILD_DIR/switch/sphaira/sphaira.nro" "$BUILD_DIR/hbmenu.nro"
}

ThemeInjector() {
    download_url=$(curl -s https://api.github.com/repos/exelix11/SwitchThemeInjector/releases/latest | jq -r ".assets[0].browser_download_url")
    curl -O -L $download_url --output-dir $TMP_DIR
    cp $TMP_DIR/NXThemesInstaller.nro -d $BUILD_DIR/$HOMEBREW_DIR
}

ThemezerNX() {
    download_url=$(curl -s https://api.github.com/repos/suchmememanyskill/themezer-nx/releases/latest | jq -r ".assets[0].browser_download_url")
    curl -O -L $download_url --output-dir $TMP_DIR
    cp $TMP_DIR/themezer-nx.nro -d $BUILD_DIR/$HOMEBREW_DIR
}

DBI() {
    download_url=$(curl -s https://api.github.com/repos/rashevskyv/dbi/releases/latest | jq -r ".assets[1].browser_download_url")
    curl -O -L $download_url --output-dir $TMP_DIR
    cp $TMP_DIR/DBI.nro -d $BUILD_DIR/$HOMEBREW_DIR/DBI
}

Appstore() {
    download_url=$(curl -s https://api.github.com/repos/fortheusers/hb-appstore/releases/latest | jq -r ".assets[0].browser_download_url")
    curl -O -L $download_url --output-dir $TMP_DIR
    cp $TMP_DIR/appstore.nro -d $BUILD_DIR/$HOMEBREW_DIR
}

# Overlays
Sys-Patch() {
    download_url=$(curl -s https://api.github.com/repos/impeeza/sys-patch/releases/latest | jq -r ".assets[0].browser_download_url")
    curl -O -L $download_url --output-dir $TMP_DIR
    $UNZIP_COMMAND $TMP_DIR/sys-patch*.zip -d $BUILD_DIR/
}

Edizon() {
    download_url=$(curl -s https://api.github.com/repos/proferabg/Edizon-Overlay/releases/latest | jq -r ".assets[0].browser_download_url")
    curl -O -L $download_url --output-dir $TMP_DIR
    $UNZIP_COMMAND $TMP_DIR/EdiZon-Overlay.zip -d $BUILD_DIR/
}

FPSLocker() {
    download_url=$(curl -s https://api.github.com/repos/masagrator/FPSLocker/releases/latest | jq -r ".assets[1].browser_download_url")
    curl -O -L $download_url --output-dir $TMP_DIR
    cp $TMP_DIR/FPSLocker.ovl -d $BUILD_DIR/$OVERLAY_DIR
}

ReverseNX() {
    download_url=$(curl -s https://api.github.com/repos/masagrator/ReverseNX-RT/releases/latest | jq -r ".assets[0].browser_download_url")
    curl -O -L $download_url --output-dir $TMP_DIR
    cp $TMP_DIR/ReverseNX-RT-ovl.ovl -d $BUILD_DIR/$OVERLAY_DIR
}

SysModules() {
    download_url=$(curl -s https://api.github.com/repos/ppkantorski/ovl-sysmodules/releases/latest | jq -r ".assets[0].browser_download_url")
    curl -O -L $download_url --output-dir $TMP_DIR
    cp $TMP_DIR/ovlSysmodules.ovl -d $BUILD_DIR/$OVERLAY_DIR
}

Sys-Clk() {
    download_url=$(curl -s https://api.github.com/repos/retronx-team/sys-clk/releases/latest | jq -r ".assets[0].browser_download_url")
    curl -O -L $download_url --output-dir $TMP_DIR
    $UNZIP_COMMAND $TMP_DIR/sys-clk*.zip -x README.md -d $BUILD_DIR/
}

Emuiibo() {
    download_url=$(curl -s https://api.github.com/repos/XorTroll/emuiibo/releases/latest | jq -r ".assets[0].browser_download_url")
    curl -O -L $download_url --output-dir $TMP_DIR
    $UNZIP_COMMAND $TMP_DIR/emuiibo.zip -d $TMP_DIR
    cp -r $TMP_DIR/SdOut/* $BUILD_DIR
}

StatusMonitor() {
    download_url=$(curl -s https://api.github.com/repos/masagrator/Status-Monitor-Overlay/releases/latest | jq -r ".assets[1].browser_download_url")
    curl -O -L $download_url --output-dir $TMP_DIR
    $UNZIP_COMMAND $TMP_DIR/Status-Monitor-Overlay.zip -d $BUILD_DIR/
}

Ultrahand() {
    download_url=$(curl -s https://api.github.com/repos/ppkantorski/Ultrahand-Overlay/releases/latest | jq -r ".assets[1].browser_download_url")
    curl -O -L $download_url --output-dir $TMP_DIR
    cp $TMP_DIR/ovlmenu.ovl -d $BUILD_DIR/$OVERLAY_DIR
}

MissionControl() {
    download_url=$(curl -s https://api.github.com/repos/ndeadly/MissionControl/releases/latest | jq -r ".assets[0].browser_download_url")
    curl -O -L $download_url --output-dir $TMP_DIR
    $UNZIP_COMMAND $TMP_DIR/MissionControl*.zip -d $BUILD_DIR/
}

mkdir $TMP_DIR
mkdir -p $OVERLAY_DIR

# Bootloaders
Hekate

# Homebrews
8BU
HBLoader
Sphaira
ThemeInjector
ThemezerNX
SaltyNX

# Overlays
Ovlloader
QuickNTP
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

rm -rf $TMP_DIR
