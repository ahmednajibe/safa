# safa.spec — RPM for Safa (M10d wave 2). Same layout as the .deb:
# /opt/safa payload + /usr/bin/safa wrapper + .desktop + icon.
# Built from the portable stage tarball (package-unix.sh):
#
#   rpmbuild -bb packaging/rpm/safa.spec \
#     --define 'safaver 0.1.0' --define 'safavariant cpu' \
#     --define '_sourcedir <dir holding tarball + safa + safa.desktop + safa.png>' \
#     --define '_rpmdir dist_pkg/rpm'
#
# ffmpeg is bundled (tools/ffmpeg) — no Requires on a system ffmpeg, so
# the package works without RPM Fusion. User data lives in
# ~/.local/share/safa-data and survives uninstall (no %postun deletion).

Name:           %{safapkg}
Version:        %{safaver}
Release:        1%{?dist}
Summary:        Clean audio and video on your own computer

License:        MIT
URL:            https://github.com/ahmednajibe/safa
Source0:        Safa-%{safaver}-linux-x64-%{safavariant}.tar.gz
Source1:        safa
Source2:        safa.desktop
Source3:        safa.png

# one safa variant at a time — both install the same paths
Conflicts:      safa
Conflicts:      safa-cuda
Provides:       safa-app

%description
Safa cuts silence, removes noise, levels loudness, separates voice from
music and writes subtitles — locally, nothing is uploaded (the app only
listens on 127.0.0.1). The Python runtime and ffmpeg are bundled; user
data lives in ~/.local/share/safa-data and is never removed by rpm -e.

%prep
%setup -q -n Safa-%{safaver}-linux-x64-%{safavariant}

%build
# nothing to compile — the stage ships a standalone python + wheels

%install
mkdir -p %{buildroot}/opt/safa %{buildroot}%{_bindir} \
         %{buildroot}%{_datadir}/applications \
         %{buildroot}%{_datadir}/icons/hicolor/256x256/apps
cp -a . %{buildroot}/opt/safa/
rm -f %{buildroot}/opt/safa/portable.txt   # system pkg → installed mode
install -Dm755 %{SOURCE1} %{buildroot}%{_bindir}/safa
install -Dm644 %{SOURCE2} %{buildroot}%{_datadir}/applications/safa.desktop
install -Dm644 %{SOURCE3} \
    %{buildroot}%{_datadir}/icons/hicolor/256x256/apps/safa.png

%files
/opt/safa
%{_bindir}/safa
%{_datadir}/applications/safa.desktop
%{_datadir}/icons/hicolor/256x256/apps/safa.png

%post
# refresh caches — best effort
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q %{_datadir}/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q %{_datadir}/icons/hicolor || true
fi
exit 0

%postun
# NEVER delete ~/.local/share/safa-data — user data survives removal.
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q %{_datadir}/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q %{_datadir}/icons/hicolor || true
fi
exit 0

%changelog
* Wed Nov 19 2025 Safa <https://github.com/ahmednajibe/safa> - 0.1.0-1
- first packaged release (M10d wave 2)
