#!/bin/bash
# 
# Make a tarball containing native (C++) code.
#
# Usage:
#   ./release-native.sh <function name>

set -o nounset
set -o pipefail
set -o errexit

# adapted from build/compile.sh
# and devtools/release.sh

readonly OIL_VERSION=$(head -n 1 oil-version.txt)

make-tar() {
  local app_name='oil-native'

  local sed_expr="s,^,${app_name}-${OIL_VERSION}/,"

  local out=_release/${app_name}-${OIL_VERSION}.tar

  build/dev.sh oil-cpp

  # TODO:
  # - be more exact about files?
  #   - exclude osh-lex.re2c.h since it's intermediate
  #   - _devbuild/gen is for osh-lex
  #   - maybe mention it explicitly?
  #   - Maybe use the -I trick?
  # - Provide a way to run C++ tests?  Unit tests and smoke tests alike.
  #
  # Reorg?
  # _build/cpp/  or _gen/cpp ?  or _gen/c ?

  tar --create --transform "$sed_expr" --file $out \
    LICENSE.txt \
    README-native.txt \
    build/common.sh \
    build/mycpp.sh \
    cpp/ \
    mycpp/mylib.{cc,h} \
    _devbuild/gen/*.h \
    _devbuild/gen-cpp/ \
    _tmp/mycpp/osh_parse.cc

  xz -c $out > $out.xz

  ls -l _release
}

test-tar() {
  local tmp=_tmp/native-tar-test  # lik oil-tar-test
  rm -r -f $tmp
  mkdir -p $tmp
  cd $tmp
  tar -x < ../../_release/oil-native-$OIL_VERSION.tar

  cd oil-native-$OIL_VERSION
  build/mycpp.sh tarball-demo
}

"$@"