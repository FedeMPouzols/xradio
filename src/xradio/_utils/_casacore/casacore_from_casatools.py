"""This module serves as an API bridge from `casatools` to `python-casacore`.

Features:
 - Returns C-order numpy arrays.
 - Workaround fpr the tablerow/tablecolumn-related API differences essential for the `xradio` use case.

Note: not fully implemented; not intended to be a full API adapter layer.
"""

import ast
import inspect
import shutil
from functools import wraps
from typing import Any, Dict, List, Union, Sequence
import os

import casatools
import numpy as np
import toolviper.utils.logger as logger


def _wrap_table(swig_object: Any) -> "table":
    """Wraps a SWIG table object.

    Parameters
    ----------
    swig_object : Any
        The SWIG object to wrap.

    Returns
    -------
    table
        The wrapped table object.
    """
    return table(swig_object=swig_object)


def method_wrapper(method: Any) -> Any:
    """Wraps a method to recursively transpose NumPy array results.

    Parameters
    ----------
    method : callable
        The method to wrap.

    Returns
    -------
    callable
        The wrapped method.
    """

    @wraps(method)
    def wrapped(*args, **kwargs):
        ret = method(*args, **kwargs)
        return recursive_transpose(ret)

    return wrapped


def recursive_transpose(val: Any) -> Any:
    """Recursively transposes all NumPy arrays within the given object.

    Parameters
    ----------
    val : Any
        The object to process. It can be a dictionary, list, NumPy array, or other object.

    Returns
    -------
    Any
        The modified object with all NumPy arrays transposed.
    """
    if isinstance(val, np.ndarray) and val.flags.f_contiguous:
        return val.T
    elif isinstance(val, list):
        return [recursive_transpose(item) for item in val]
    elif isinstance(val, dict):
        return {key: recursive_transpose(value) for key, value in val.items()}
    else:
        return val


def wrap_class_methods(cls: type) -> type:
    """Class decorator to wrap all methods of a class, including inherited ones.

    Parameters
    ----------
    cls : type
        The class to wrap.

    Returns
    -------
    type
        The class with its methods wrapped.
    """
    for name, method in inspect.getmembers(cls, predicate=inspect.isfunction):
        if callable(method):
            setattr(cls, name, method_wrapper(method))
    return cls


@wrap_class_methods
class table(casatools.table):
    """A wrapper for the casatools table object.

    Parameters
    ----------
    tablename : str, optional
        The name of the table.
    tabledesc : bool, optional
        Table description.
    nrow : int, optional
        Number of rows.
    readonly : bool, optional
        Whether the table is read-only.
    lockoptions : dict, optional
        Locking options.
    ack : bool, optional
        Acknowledgment flag.
    dminfo : dict, optional
        Data manager information.
    endian : str, optional
        Endian type.
    memorytable : bool, optional
        Whether the table is in memory.
    concatsubtables : list, optional
        Concatenated subtables.
    """

    def __init__(
        self,
        tablename: str = "",
        tabledesc: bool = False,
        nrow: int = 0,
        readonly: bool = True,
        lockoptions: Dict = {},
        ack: bool = True,
        dminfo: Dict = {},
        endian: str = "aipsrc",
        memorytable: bool = False,
        concatsubtables: List = [],
        **kwargs,
    ):
        super().__init__(tablename=tablename, lockoptions=lockoptions, nomodify=True, **kwargs)

    def row(self, columnnames: List[str] = [], exclude: bool = False) -> "tablerow":
        """Access rows in the table.

        Parameters
        ----------
        columnnames : list of str, optional
            Column names to include or exclude.
        exclude : bool, optional
            Whether to exclude the specified columns.

        Returns
        -------
        tablerow
            A tablerow object for accessing rows.
        """
        return tablerow(self, columnnames=columnnames, exclude=exclude)

    def col(self, columnname: str) -> "tablecolumn":
        """Access a specific column in the table.

        Parameters
        ----------
        columnname : str
            The name of the column to access.

        Returns
        -------
        tablecolumn
            A tablecolumn object for accessing column data.
        """
        return tablecolumn(self, columnname)

    def taql(self, taqlcommand="TaQL expression"):
        """Expose TaQL (Table Query Language) to the user.

        This method allows the execution of a TaQL expression on the table.
        It substitutes `$mtable` and `$gtable` in the provided `taqlcommand`
        with the current table name. A temporary copy of the table is created
        if it is not currently opened.

        Parameters
        ----------
        taqlcommand : str, optional
            The TaQL expression to execute. Default is `'TaQL expression'`.

        Returns
        -------
        tb_query_to : object
            The result of the TaQL query as a wrapped table object.

        Notes
        -----
        For more details on TaQL, refer to:
        https://casacore.github.io/casacore-notes/199.html

        Examples
        --------
        >>> result_table = obj.taql('SELECT * FROM $mtable WHERE col1 > 5')
        >>> print(result_table.name())
        """
        is_open = self.isopened(self.name())
        if not is_open:
            tablename = self.name() + "_copy"
            tb_query_from = self.copy(tablename, deep=False, valuecopy=False)
        else:
            tablename = self.name()
            tb_query_from = self
        tb_query = taqlcommand.replace("$mtable", tablename).replace("$gtable", tablename)
        logger.debug(f"tb_query_from: {tb_query_from.name()}")
        logger.debug(f"tb_query_cmd:  {tb_query}")
        tb_query_to = _wrap_table(swig_object=tb_query_from._swigobj.taql(tb_query))
        if not is_open:
            tb_query_from.close()
            shutil.rmtree(tablename)
        logger.debug(f"tb_query_to: {tb_query_to.name()}")
        return tb_query_to

    def getcolshapestring(self, *args, **kwargs):
        """Get the shape of table columns as string representations.

        This method retrieves the shapes of table columns and formats them as
        reversed string representations of the shapes. It is useful for viewing
        column dimensions in a human-readable format.

        Parameters
        ----------
        *args : tuple
            Positional arguments to pass to the superclass method.
        **kwargs : dict
            Keyword arguments to pass to the superclass method.

        Returns
        -------
        list of str
            A list of reversed shapes as strings.

        Examples
        --------
        >>> shapes = obj.getcolshapestring()
        >>> print(shapes)
        ['[10, 5]', '[20, 15]']
        """
        ret = super().getcolshapestring(*args, **kwargs)
        return [str(list(reversed(ast.literal_eval(shape)))) for shape in ret]

    def getcellslice(self, columnname, rownr, blc, trc, incr=1):
        """Retrieve a sliced portion of a cell from a specified column.

        This method extracts a subarray from a cell within a table column, 
        given the bottom-left corner (BLC) and top-right corner (TRC) indices.
        It also supports an optional increment (`incr`) to control step size.

        Parameters
        ----------
        columnname : str
            The name of the column from which to extract data.
        rownr : int or Sequence[int]
            The row number(s) from which to extract data. If a sequence is provided, 
            it is reversed before processing.
        blc : Sequence[int]
            The bottom-left corner indices of the slice.
        trc : Sequence[int]
            The top-right corner indices of the slice.
        incr : int or Sequence[int], optional
            Step size for slicing. If a sequence is provided, it is reversed. 
            If a single integer is given, it is expanded to match `blc` dimensions. 
            Defaults to 1.

        Returns
        -------
        Any
            The extracted slice from the specified column and row(s).

        Notes
        -----
        - If `rownr` is a sequence, it is reversed before processing.
        - The `blc`, `trc`, and `incr` parameters are converted to lists of integers.
        - Calls the superclass method `getcellslice` for actual data retrieval.
        """

        if isinstance(rownr, Sequence):
            rownr = rownr[::-1]
        else:
            rownr = [rownr]*len(blc)
        rownr = 0
        if isinstance(blc, Sequence):
            blc = list(map(int, blc[::-1]))
        if isinstance(trc, Sequence):
            trc = list(map(int, trc[::-1]))
        if isinstance(incr, Sequence):
            incr = incr[::-1]
        else:
            incr = [incr]*len(blc)
        ret = super().getcellslice(columnname=columnname, rownr=rownr, blc=blc, trc=trc)
        return ret


@wrap_class_methods
class image(casatools.image):
    """A Wrapper class around `casatools.image` that provides python-casacore-like methods."""

    def __init__(
        self,
        imagename,
        axis=0,
        maskname="",
        images=(),
        values=None,
        coordsys=None,
        overwrite=True,
        ashdf5=False,
        mask=(),
        shape=None,
        tileshape=(),
    ):
        super().__init__()
        self._imagename = imagename
        if shape is None:
            # self.open(*arg, **kwargs)
            self.open(imagename)
        else:
            if value is None:
                self.newimagefromshape(outfile, shape=shape)
            else:
                self.newimagefromarray(outfile, pixels=self.makearray(value, shape))

    def getdata(self, blc=None, trc=None, inc=None):
        """Retrieve image data as a chunk.

        Parameters
        ----------
        blc : list of int, optional
            Bottom-left corner of the region to extract. Defaults to `[-1]` (entire image).
        trc : list of int, optional
            Top-right corner of the region to extract. Defaults to `[-1]` (entire image).
        inc : list of int, optional
            Step size for slicing. Defaults to `[1]`.

        Returns
        -------
        numpy.ndarray
            The extracted data chunk.
        """

        if blc is None:
            blc = [-1]
        if trc is None:
            trc = [-1]
        if inc is None:
            inc = [1]
        return super().getchunk(blc, trc, inc)

    def shape(self):
        """Get the shape of the image.

        Returns
        -------
        list of int
            The shape of the image, with axes reversed for consistency.
        """
        return list(map(int, super().shape()[::-1]))

    def coordinates(self):
        """Get the coordinate system of the image.

        Returns
        -------
        casatools.coordinatesystem
            The coordinate system associated with the image.
        """
        return coordinatesystem(self)

    def unit(self):
        """Get the brightness unit of the image.

        Returns
        -------
        str
            The brightness unit of the image.
        """
        return self.brightnessunit()

    def info(self):
        """Retrieve image metadata including coordinates, misc info, and beam information.

        Returns
        -------
        dict
            Dictionary containing:
            - 'imageinfo': Flattened image summary.
            - 'coordinates': Coordinate system as a dictionary.
            - 'miscinfo': Miscellaneous metadata.
        """
        imageinfo = self.summary(list=False)
        imageinfo = self._flatten_multibeam(imageinfo)
        # table.getdesc()

        return {'imageinfo': imageinfo,
                'coordinates': self.coordsys().torecord(),
                'miscinfo': self.miscinfo()}

    def datatype(self):
        return self.pixeltype()

    def _flatten_multibeam(self, imageinfo):
        """Flatten the per-plane beam information in the image metadata.

        This method restructures the `perplanebeams` field in `imageinfo`
        to make it more accessible by flattening the nested structure.

        Parameters
        ----------
        imageinfo : dict
            The image metadata containing per-plane beam information.

        Returns
        -------
        dict
            Updated `imageinfo` dictionary with flattened per-plane beam data.
        """
        if 'perplanebeams' in imageinfo:
            perplanebeams = imageinfo['perplanebeams']['beams']
            perplanebeams_flat = {}
            nchan = imageinfo['perplanebeams']['nChannels']
            npol = imageinfo['perplanebeams']['nStokes']

            for c in range(nchan):
                for p in range(npol):
                    k = nchan * p + c
                    perplanebeams_flat["*" + str(k)] = perplanebeams['*'+str(c)]['*'+str(p)]
            imageinfo['perplanebeams'].pop('beams', None)
            imageinfo['perplanebeams'].update(perplanebeams_flat)

        return imageinfo


class coordinatesystem(casatools.coordsys):
    """A wrapper around `casatools.coordsys` that provides python-casacore like methods"""

    def __init__(self, image=None):
        self._image = image
        if image is None:
            self._cs = casatools.coordsys()
        else:
            self._cs = image.coordsys()

    def get_axes(self):
        """Retrieve the names of the coordinate axes.

        Returns
        -------
        list of str or list of lists
            A list containing the names of each axis, grouped by coordinate type.
            Spectral axes are returned as a single string instead of a list.
        """
        axes = []
        axis_names = self._cs.names()
        for axis_type in self.get_names():
            axis_inds = self._cs.findcoordinate(axis_type).get('pixel')
            axes_list = [axis_names[idx] for idx in axis_inds[::-1]]
            if axis_type == 'spectral':
                axes_list = axes_list[0]
            axes.append(axes_list)
        return axes

    def get_referencepixel(self):
        """Get the reference pixel coordinates.

        Returns
        -------
        list of float
            The numeric reference pixel values, with axes reversed.
        """
        return self._cs.referencepixel()['numeric'][::-1]

    def get_referencevalue(self):
        """Get the reference value at the reference pixel.

        Returns
        -------
        list of float
            The numeric reference values, with axes reversed.
        """
        return self._cs.referencevalue()['numeric'][::-1]

    def get_increment(self):
        """Get the coordinate increments per pixel.

        Returns
        -------
        list of float
            The coordinate increment values, with axes reversed.
        """
        return self._cs.increment()['numeric'][::-1]

    def get_unit(self):
        """Get the units of the coordinate axes.

        Returns
        -------
        list of str
            The units of each axis, with axes reversed.
        """
        return self._cs.units()[::-1]

    def get_names(self):
        """Get the coordinate type names in lowercase.

        Returns
        -------
        list of str
            The coordinate type names, with axes reversed.
        """
        return list(map(str.lower, self._cs.coordinatetype()[::-1]))

    def dict(self):
        """Convert the coordinate system to a dictionary representation.

        Returns
        -------
        dict
            The coordinate system in CASA's dictionary format.
        """
        return self._cs.torecord()


class directioncoordinate(coordinatesystem):

    def __init__(self, rec):
        super().__init__()
        self._rec = rec

    def get_projection(self):
        return self._rec['projection']


@wrap_class_methods
class tablerow(casatools.tablerow):
    """A wrapper for the casatools tablerow object.

    Parameters
    ----------
    table : table
        The table object to wrap.
    columnnames : list of str, optional
        Column names to include or exclude.
    exclude : bool, optional
        Whether to exclude the specified columns.
    """

    def __init__(self, table: table, columnnames: List[str] = [], exclude: bool = False):
        super().__init__(table, columnnames=columnnames, exclude=exclude)

    @method_wrapper
    def get(self, rownr: int) -> Dict[str, Any]:
        """Retrieve data for a specific row.

        Parameters
        ----------
        rownr : int
            The row number to retrieve.

        Returns
        -------
        dict
            A dictionary containing row data.
        """
        return super().get(rownr)

    def __getitem__(self, key: Union[int, slice]) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
        """Retrieve rows using indexing or slicing.

        Parameters
        ----------
        key : int or slice
            The row index or slice to retrieve.

        Returns
        -------
        dict or list of dict
            The row(s) corresponding to the key.
        """
        if isinstance(key, slice):
            return [self.get(irow) for irow in range(*key.indices(len(self)))]
        elif isinstance(key, int):
            return self.get(key)


@wrap_class_methods
class tablecolumn:
    """A class representing a single column in a table.

    Provides methods to access values in the column with indexing and slicing.

    Parameters
    ----------
    table : Any
        The table object containing the column.
    columnname : str
        The name of the column in the table.

    Attributes
    ----------
    _table : Any
        The table object containing the column.
    _columnname : str
        The name of the column in the table.
    """

    def __init__(self, table: Any, columnname: str):
        self._table = table
        self._columnname = columnname

    @method_wrapper
    def get(self, irow: int) -> Any:
        """Get the value at a specific row in the column.

        Parameters
        ----------
        irow : int
            The index of the row to retrieve.

        Returns
        -------
        Any
            The value in the specified row of the column.
        """
        return self._table.getcell(self._columnname, irow)

    def __getitem__(self, key: Union[int, slice]) -> Union[Any, List[Any]]:
        """Get a value or a list of values from the column using indexing or slicing.

        Parameters
        ----------
        key : int or slice
            The index or slice to retrieve values from the column.

        Returns
        -------
        Any or list of Any
            The value(s) retrieved from the column.

        Examples
        --------
        >>> table = MockTable()
        >>> column = TableColumn(table, 'col1')
        >>> column[0]  # Get the first row's value
        42
        >>> column[1:3]  # Get values from rows 1 to 2
        [43, 44]
        """
        if isinstance(key, slice):
            return [self.get(irow) for irow in range(*key.indices(self._table.nrows()))]
        elif isinstance(key, int):
            return self.get(key)


def tableexists(path):
    return os.path.isdir(path)
