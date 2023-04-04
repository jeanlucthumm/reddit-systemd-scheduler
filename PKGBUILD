# Maintainer: Jean-Luc Thumm <jeanlucthumm@gmail.com>
pkgname=reddit-systemd-scheduler
pkgver=1.0.0
pkgrel=1
pkgdesc="systemd service for scheduling posts to reddit"
url="https://github.com/jeanlucthumm/reddit-systemd-scheduler"
arch=("x86_64")
makedepends=("git" "python")
license=("GPL3")
source=("$pkgname-$pkgver::https://github.com/jeanlucthumm/$pkgname/archive/v$pkgver.tar.gz")
# Skipped only in GitHub repo to avoid recursion
sha512sums=("SKIP")

build() {
  cd "$pkgname-$pkgver"
  make
}

package() {
  cd "$pkgname-$pkgver"
  make DESTDIR="$pkgdir" install
}
