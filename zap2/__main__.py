# -*- coding: utf-8 -*-

import argparse
import logging
import sys

from .version import __version__, __description__
from .zap import process, CFTYPE_OPTIONS


def main():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description=__description__
    )
    addarg = parser.add_argument
    addarg('incube', help='Input datacube path')
    addarg('--version', '-V', action='version',
           version='%(prog)s ' + __version__)
    addarg('--debug', '-d', action='store_true',
           help='show debug info')
    addarg('--no-clean', action='store_true',
           help='disable NaN values interpolation')
    addarg('--outcube', '-o', default='DATACUBE_FINAL_ZAP.fits',
           help='output datacube path')
    addarg('--mask', help='Mask file to exclude sources')
    addarg('--skycube', help='Sky datacube path')
    addarg('--extsvd',
           help='Path of an input FITS file containing a SVD computed in a '
           'previous step')
    addarg('--cfwidthSVD', type=int, default=100,
           help='window size for the continuum filter for the SVD computation')
    addarg('--cfwidthSP', type=int, default=50,
           help='window size for the continuum filter')
    addarg('--zlevel', default='median',
           help='method for the zeroth order sky removal: none, sigclip or '
           'median')
    addarg('--cftype', default='weight',
           help='method for the continuum filter: {}. For the '
           'weight method, a zeroth order sky is required (see zlevel)'
           .format(', '.join(CFTYPE_OPTIONS)))
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        process(
            args.incube, outcubefits=args.outcube, clean=not args.no_clean,
            skycubefits=args.skycube,
            mask=args.mask, extSVD=args.extsvd, cfwidthSVD=args.cfwidthSVD,
            cfwidthSP=args.cfwidthSP, zlevel=args.zlevel, cftype=args.cftype)
    except KeyboardInterrupt:
        sys.exit('Interrupted!')
    except Exception as e:
        if args.debug:
            import traceback
            traceback.print_exc()
        sys.exit('Failed to process file: %s' % e)


if __name__ == "__main__":
    main()
