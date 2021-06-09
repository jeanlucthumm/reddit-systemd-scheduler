# Maintainer: Jean-Luc Thumm <jeanlucthumm@gmail.com>
pkgname=reddit-systemd-scheduler-git
pkgver=r57.e2b3977
pkgrel=1
pkgdesc="systemd service for scheduling posts to reddit"
url="https://github.com/jeanlucthumm/reddit-systemd-scheduler"
arch=("any")
makedepends=("pyinstaller" "git" "python")
provides=("reddit")
source=("${pkgname%-*}::git+git://github.com/jeanlucthumm/${pkgname%-*}.git")
sha1sums=("SKIP")

pkgver() {
  cd "${pkgname%-*}"
  printf "r%s.%s" "$(git rev-list --count HEAD)" "$(git rev-parse --short HEAD)"
}

build() {
  cd "${pkgname%-*}"
  make
}

package() {
  cd "${pkgname%-*}"
  make DESTDIR="$pkgdir/" install
}
