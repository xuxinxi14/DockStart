"""
Module containing implementation of Gaussian-based shape overlay and scoring.NOTE: This functionality is experimental and the API and/or results may change in future releases.
"""
from __future__ import annotations
import typing
__all__: list[str] = ['A_LA_PUBCHEM', 'AlignMol', 'AlignShapes', 'OptimMode', 'ROTATE_0', 'ROTATE_0_FRAGMENT', 'ROTATE_180', 'ROTATE_180_FRAGMENT', 'ROTATE_180_WIGGLE', 'ROTATE_45', 'ROTATE_45_FRAGMENT', 'SHAPE_ONLY', 'SHAPE_PLUS_COLOR', 'SHAPE_PLUS_COLOR_SCORE', 'ScoreMol', 'ScoreShape', 'ShapeInput', 'ShapeInputOptions', 'ShapeOverlayOptions', 'StartMode']
class OptimMode(Boost.Python.enum):
    SHAPE_ONLY: typing.ClassVar[OptimMode]  # value = rdkit.Chem.rdGaussianShape.OptimMode.SHAPE_ONLY
    SHAPE_PLUS_COLOR: typing.ClassVar[OptimMode]  # value = rdkit.Chem.rdGaussianShape.OptimMode.SHAPE_PLUS_COLOR
    SHAPE_PLUS_COLOR_SCORE: typing.ClassVar[OptimMode]  # value = rdkit.Chem.rdGaussianShape.OptimMode.SHAPE_PLUS_COLOR_SCORE
    __slots__: typing.ClassVar[tuple] = tuple()
    names: typing.ClassVar[dict]  # value = {'SHAPE_ONLY': rdkit.Chem.rdGaussianShape.OptimMode.SHAPE_ONLY, 'SHAPE_PLUS_COLOR_SCORE': rdkit.Chem.rdGaussianShape.OptimMode.SHAPE_PLUS_COLOR_SCORE, 'SHAPE_PLUS_COLOR': rdkit.Chem.rdGaussianShape.OptimMode.SHAPE_PLUS_COLOR}
    values: typing.ClassVar[dict]  # value = {0: rdkit.Chem.rdGaussianShape.OptimMode.SHAPE_ONLY, 1: rdkit.Chem.rdGaussianShape.OptimMode.SHAPE_PLUS_COLOR_SCORE, 2: rdkit.Chem.rdGaussianShape.OptimMode.SHAPE_PLUS_COLOR}
class ShapeInput(Boost.Python.instance):
    """
    ShapeInput object
    """
    __instance_size__: typing.ClassVar[int] = 288
    @staticmethod
    def __reduce__(*args, **kwargs):
        ...
    @staticmethod
    def __setattr__(arg1: typing.Any, arg2: str, arg3: typing.Any) -> None:
        """
            C++ signature :
                void __setattr__(class boost::python::api::object,class std::basic_string<char,struct std::char_traits<char>,class std::allocator<char> >,class boost::python::api::object)
        """
    def __init__(self, self: Mol, confId: int, shapeOpt: ShapeInputOptions, overlayOpts: ShapeOverlayOptions) -> None:
        """
            C++ signature :
                void __init__(struct _object * __ptr64,class RDKit::ROMol,int,struct RDKit::GaussianShape::ShapeInputOptions,struct RDKit::GaussianShape::ShapeOverlayOptions)
        """
    @property
    def ColorVolume(*args, **kwargs):
        """
        Get the volume of the shape's color features.
        """
    @property
    def NumAtoms(*args, **kwargs):
        """
        Get the number of atoms defining the shape.
        """
    @property
    def NumFeatures(*args, **kwargs):
        """
        Get the number of features in the shape.
        """
    @property
    def ShapeVolume(*args, **kwargs):
        """
        Get the shape's volume due to the atoms.
        """
class ShapeInputOptions(Boost.Python.instance):
    """
    ShapeInputOptions - options for setting up ShapeInput objects.
    """
    __instance_size__: typing.ClassVar[int] = 112
    @staticmethod
    def __reduce__(*args, **kwargs):
        ...
    @staticmethod
    def __setattr__(arg1: typing.Any, arg2: str, arg3: typing.Any) -> None:
        """
            C++ signature :
                void __setattr__(class boost::python::api::object,class std::basic_string<char,struct std::char_traits<char>,class std::allocator<char> >,class boost::python::api::object)
        """
    def __init__(self) -> None:
        """
            C++ signature :
                void __init__(struct _object * __ptr64)
        """
    @property
    def allCarbonRadii(*args, **kwargs):
        """
        Whether to use the same radius, appropriate for Carbon, for all atoms.  There is a slight accuracy penalty but significant speed gain if used.  Default=True.
        """
    @allCarbonRadii.setter
    def allCarbonRadii(*args, **kwargs):
        ...
    @property
    def atomRadii(*args, **kwargs):
        """
        Non-standard radii to use for the atoms specified by their indices in the molecule.  Not all atoms need have a radius specified.  A list of tuples of [int, float].
        """
    @atomRadii.setter
    def atomRadii(*args, **kwargs):
        ...
    @property
    def atomSubset(*args, **kwargs):
        """
        If not empty, use just these atoms in the molecule to form the ShapeInput object.
        """
    @atomSubset.setter
    def atomSubset(*args, **kwargs):
        ...
    @property
    def customFeatures(*args, **kwargs):
        """
        Custom features for the shape.  Requires a list of tuples of int (the feature type), Point3D (the coordinates) and float (the radius).
        """
    @customFeatures.setter
    def customFeatures(*args, **kwargs):
        ...
    @property
    def useColors(*args, **kwargs):
        """
        Whether to use color features in overlay.  Default=True.
        """
    @useColors.setter
    def useColors(*args, **kwargs):
        ...
class ShapeOverlayOptions(Boost.Python.instance):
    """
    ShapeOverlayOptions - options for controlling the shape overlay process.
    """
    __instance_size__: typing.ClassVar[int] = 80
    @staticmethod
    def __reduce__(*args, **kwargs):
        ...
    @staticmethod
    def __setattr__(arg1: typing.Any, arg2: str, arg3: typing.Any) -> None:
        """
            C++ signature :
                void __setattr__(class boost::python::api::object,class std::basic_string<char,struct std::char_traits<char>,class std::allocator<char> >,class boost::python::api::object)
        """
    def __init__(self) -> None:
        """
            C++ signature :
                void __init__(struct _object * __ptr64)
        """
    @property
    def distCutoff(*args, **kwargs):
        """
        If using a distance cutoff, this is the value used.  Default=4.5 of whatever units the coordinates are in.
        """
    @distCutoff.setter
    def distCutoff(*args, **kwargs):
        ...
    @property
    def nSteps(*args, **kwargs):
        """
        Maximum number of steps for the shape overlay process. Default=100.
        """
    @nSteps.setter
    def nSteps(*args, **kwargs):
        ...
    @property
    def normalize(*args, **kwargs):
        """
        Whether to normalize the shapes before overlay by putting them into their canonical orientation (centred on the origin, aligned along its principal axes.  Default=True.
        """
    @normalize.setter
    def normalize(*args, **kwargs):
        ...
    @property
    def optParam(*args, **kwargs):
        """
        If using colors, the relative weights of the shape and color scores, as a fraction of 1.  Default=0.5.
        """
    @optParam.setter
    def optParam(*args, **kwargs):
        ...
    @property
    def optimMode(*args, **kwargs):
        """
        Optimisation mode, controlling what parameters are used to drive the overlay.  Default=SHAPE_PLUS_COLOR_SCORE which optimises using just the overlap of shape, but uses the color to decide which is the best overlay.  Other options are SHAPE_ONLY and SHAPE_AND_COLOR with the latter using the overlap of color features as well. 
        """
    @optimMode.setter
    def optimMode(*args, **kwargs):
        ...
    @property
    def shapeConvergenceCriterion(*args, **kwargs):
        """
        Optimisation stops when the shape Tversky score changes by less than this amount after an optimisation step.  A larger number is faster but gives less precise overlays.  Default=0.001.
        """
    @shapeConvergenceCriterion.setter
    def shapeConvergenceCriterion(*args, **kwargs):
        ...
    @property
    def simAlpha(*args, **kwargs):
        """
        When doing a Tversky similarity, the alpha value.  If alpha and beta are both the default 1.0, it's a Tanimoto similarity.  A high alpha and low beta emphasize the fit volume in the similarity and vice versa. Tversky is O / (A * (R - O) + B * (F - O) + O) where O is the overlap volume, R is the reference's volume and F is the fit's volume.  This is different from that used by OpenEye (O / (A * R + B * F)).
        """
    @simAlpha.setter
    def simAlpha(*args, **kwargs):
        ...
    @property
    def simBeta(*args, **kwargs):
        """
        When doing a Tversky similarity, the beta value.
        """
    @simBeta.setter
    def simBeta(*args, **kwargs):
        ...
    @property
    def startMode(*args, **kwargs):
        """
        Start modes for optimisation.  Default is A_LA_PUBCHEM - as used by the PubChem code - either ROTATE_180_WIGGLE or ROTATE_45 depending on the shape of the two molecules.  ROTATE_180_WIGGLE means 180 rotations about the x, y and z axes, then a small rotation about each axis from that point, using the best scoring one of those. ROTATE_180 uses 180 degree rotations for 4 start points, ROTATE_45 uses 45 degree rotations for 9 start points and ROTATE_0 leaves the relative orientations of the 2 molecules as passed in before optimisation.  There are also ROTATE_0_FRAGMENT, ROTATE_45_FRAGMENT and ROTATE_180_FRAGMENT that as well as the above move the fit molecule to the ends of each of the principal axes and then does the appropriate rotations.  This is useful when the fit molecule is a lot smaller than the reference molecule, but requires a large number of optimisations so is relatively slow.
        """
    @startMode.setter
    def startMode(*args, **kwargs):
        ...
    @property
    def useDistCutoff(*args, **kwargs):
        """
        Whether to use distance cutoff when calculating the shape volumes.  If used, there will be a small penalty in accuracy but a significant increase in speed.  Default=True.
        """
    @useDistCutoff.setter
    def useDistCutoff(*args, **kwargs):
        ...
class StartMode(Boost.Python.enum):
    A_LA_PUBCHEM: typing.ClassVar[StartMode]  # value = rdkit.Chem.rdGaussianShape.StartMode.A_LA_PUBCHEM
    ROTATE_0: typing.ClassVar[StartMode]  # value = rdkit.Chem.rdGaussianShape.StartMode.ROTATE_0
    ROTATE_0_FRAGMENT: typing.ClassVar[StartMode]  # value = rdkit.Chem.rdGaussianShape.StartMode.ROTATE_0_FRAGMENT
    ROTATE_180: typing.ClassVar[StartMode]  # value = rdkit.Chem.rdGaussianShape.StartMode.ROTATE_180
    ROTATE_180_FRAGMENT: typing.ClassVar[StartMode]  # value = rdkit.Chem.rdGaussianShape.StartMode.ROTATE_180_FRAGMENT
    ROTATE_180_WIGGLE: typing.ClassVar[StartMode]  # value = rdkit.Chem.rdGaussianShape.StartMode.ROTATE_180_WIGGLE
    ROTATE_45: typing.ClassVar[StartMode]  # value = rdkit.Chem.rdGaussianShape.StartMode.ROTATE_45
    ROTATE_45_FRAGMENT: typing.ClassVar[StartMode]  # value = rdkit.Chem.rdGaussianShape.StartMode.ROTATE_45_FRAGMENT
    __slots__: typing.ClassVar[tuple] = tuple()
    names: typing.ClassVar[dict]  # value = {'ROTATE_0': rdkit.Chem.rdGaussianShape.StartMode.ROTATE_0, 'ROTATE_180': rdkit.Chem.rdGaussianShape.StartMode.ROTATE_180, 'ROTATE_180_WIGGLE': rdkit.Chem.rdGaussianShape.StartMode.ROTATE_180_WIGGLE, 'ROTATE_45': rdkit.Chem.rdGaussianShape.StartMode.ROTATE_45, 'ROTATE_0_FRAGMENT': rdkit.Chem.rdGaussianShape.StartMode.ROTATE_0_FRAGMENT, 'ROTATE_180_FRAGMENT': rdkit.Chem.rdGaussianShape.StartMode.ROTATE_180_FRAGMENT, 'ROTATE_45_FRAGMENT': rdkit.Chem.rdGaussianShape.StartMode.ROTATE_45_FRAGMENT, 'A_LA_PUBCHEM': rdkit.Chem.rdGaussianShape.StartMode.A_LA_PUBCHEM}
    values: typing.ClassVar[dict]  # value = {0: rdkit.Chem.rdGaussianShape.StartMode.ROTATE_0, 1: rdkit.Chem.rdGaussianShape.StartMode.ROTATE_180, 2: rdkit.Chem.rdGaussianShape.StartMode.ROTATE_180_WIGGLE, 3: rdkit.Chem.rdGaussianShape.StartMode.ROTATE_45, 4: rdkit.Chem.rdGaussianShape.StartMode.ROTATE_0_FRAGMENT, 5: rdkit.Chem.rdGaussianShape.StartMode.ROTATE_180_FRAGMENT, 6: rdkit.Chem.rdGaussianShape.StartMode.ROTATE_45_FRAGMENT, 7: rdkit.Chem.rdGaussianShape.StartMode.A_LA_PUBCHEM}
@typing.overload
def AlignMol(ref: Mol, fit: Mol, refOpts: typing.Any = None, fitOpts: typing.Any = None, overlayOpts: typing.Any = None, refConfId: int = -1, fitConfId: int = -1) -> tuple:
    """
        Aligns a fit molecule onto a reference molecule.  The fit is modified.
        
        Parameters
        ----------
        ref: RDKit.ROMol
            Reference molecule
        fit: RDKit.ROMol
            Fit molecule that will be overlaid
        refOpts: ShapeInputOptions, optional
            Options for building the ref shape
        fitOpts: ShapeInputOptions, optional
            Options for building the fit shape
        overlayOpts: ShapeOverlayOptions, optional
            Options for controlling the overlay
        refConfId : int, optional
            Reference conformer ID (default is -1)
        fitConfId : int, optional
            fit conformer ID (default is -1)
        
        Returns
        -------
        3-tuple of floats
            The results are (combo_score, shape_score, color_score).  The color_score is
            0.0 if color features not used, in which case combo_score and shape_score will
            be the same.
        
    
        C++ signature :
            class boost::python::tuple AlignMol(class RDKit::ROMol,class RDKit::ROMol {lvalue} [,class boost::python::api::object=None [,class boost::python::api::object=None [,class boost::python::api::object=None [,int=-1 [,int=-1]]]]])
    """
@typing.overload
def AlignMol(refShape: ShapeInput, fit: Mol, fitOpts: typing.Any = None, overlayOpts: typing.Any = None, fitConfId: int = -1) -> tuple:
    """
        Aligns a fit molecule onto a reference shape.  The fit is modified.
        
        Parameters
        ----------
        refShape: ShapeInput
            Reference shape
        fit: RDKit.ROMol
            Fit molecule that will be overlaid
        fitOpts: ShapeInputOptions, optional
            Options for building the fit shape
        overlayOpts: ShapeOverlayOptions, optional
            Options for controlling the overlay
        fitConfId : int, optional
            Fit conformer ID (default is -1)
        
        Returns
        -------
        3-tuple of floats
            The results are (combo_score, shape_score, color_score).  The color_score is
            0.0 if color features not used, in which case combo_score and shape_score will
            be the same.
    
        C++ signature :
            class boost::python::tuple AlignMol(class RDKit::GaussianShape::ShapeInput,class RDKit::ROMol {lvalue} [,class boost::python::api::object=None [,class boost::python::api::object=None [,int=-1]]])
    """
def AlignShapes(refShape: ShapeInput, fitShape: ShapeInput, overlayOpts: typing.Any = None) -> tuple:
    """
        Aligns a fit shape to a reference shape. The fit is modified.
        
        Parameters
        ----------
        refShape : ShapeInput
            Reference shape
        fitShape : ShapeInput
            fit shape
        overlayOpts: ShapeOverlayOptions, optional
            Options for controlling the overlay
        
        
        Returns
        -------
         4-tuple of float, float, list of floats
            The results are (combo_score, shape_score, color_score, matrix)
            The matrix is a 16-float list giving the transformation matrix that
            overlays the fit onto the reference.
    
        C++ signature :
            class boost::python::tuple AlignShapes(class RDKit::GaussianShape::ShapeInput,class RDKit::GaussianShape::ShapeInput {lvalue} [,class boost::python::api::object=None])
    """
@typing.overload
def ScoreMol(ref: Mol, fit: Mol, refOpts: typing.Any = None, fitOpts: typing.Any = None, overlayOpts: typing.Any = None, refConfId: int = -1, fitConfId: int = -1) -> tuple:
    """
        Calculates the scores between a reference molecule and a fit
        molecule without overlay.
        
        Parameters
        ----------
        ref: RDKit.ROMol
            Reference molecule
        fit: RDKit.ROMol
            Fit molecule that will be scored
        refOpts: ShapeInputOptions, optional
            Options for building the ref shape
        fitOpts: ShapeInputOptions, optional
            Options for building the fit shape
        overlayOpts: ShapeOverlayOptions, optional
            Options for controlling the volume calculation
        refConfId : int, optional
            Reference conformer ID (default is -1)
        fitConfId : int, optional
            fit conformer ID (default is -1)
        
        Returns
        -------
        3-tuple of floats
            The results are (combo_score, shape_score, color_score).  The color_score is
            0.0 if color features not used, in which case combo_score and shape_score will
            be the same.
        
    
        C++ signature :
            class boost::python::tuple ScoreMol(class RDKit::ROMol,class RDKit::ROMol [,class boost::python::api::object=None [,class boost::python::api::object=None [,class boost::python::api::object=None [,int=-1 [,int=-1]]]]])
    """
@typing.overload
def ScoreMol(refShape: ShapeInput, fit: Mol, fitOpts: typing.Any = None, overlayOpts: typing.Any = None, fitConfId: int = -1) -> tuple:
    """
        Calculates the scores between a reference shape and a fit molecule
        without overlay.
        
        Parameters
        ----------
        refShape: ShapeInput
            Reference shape
        fit: RDKit.ROMol
            Fit molecule that will be scored
        fitOpts: ShapeInputOptions, optional
            Options for building the fit shape
        overlayOpts: ShapeOverlayOptions, optional
            Options for controlling the volume calculation
        fitConfId : int, optional
            fit conformer ID (default is -1)
        
        Returns
        -------
        3-tuple of floats
            The results are (combo_score, shape_score, color_score).  The color_score is
            0.0 if color features not used, in which case combo_score and shape_score will
            be the same.
        
    
        C++ signature :
            class boost::python::tuple ScoreMol(class RDKit::GaussianShape::ShapeInput,class RDKit::ROMol [,class boost::python::api::object=None [,class boost::python::api::object=None [,int=-1]]])
    """
def ScoreShape(refShape: ShapeInput, fitShape: ShapeInput, overlayOpts: typing.Any = None) -> tuple:
    """
        Calculates the scores between a reference shape and a fit shape without
        overlay.
        
        Parameters
        ----------
        refShape: ShapeInput
            Reference shape
        fitShape: ShapeInput
            Fit shape
        fitOpts: ShapeInputOptions, optional
            Options for building the fit shape
        overlayOpts: ShapeOverlayOptions, optional
            Options for controlling the volume calculation
        
        Returns
        -------
        3-tuple of floats
            The results are (combo_score, shape_score, color_score).  The color_score is
            0.0 if color features not used, in which case combo_score and shape_score will
            be the same.
        
    
        C++ signature :
            class boost::python::tuple ScoreShape(class RDKit::GaussianShape::ShapeInput,class RDKit::GaussianShape::ShapeInput [,class boost::python::api::object=None])
    """
A_LA_PUBCHEM: StartMode  # value = rdkit.Chem.rdGaussianShape.StartMode.A_LA_PUBCHEM
ROTATE_0: StartMode  # value = rdkit.Chem.rdGaussianShape.StartMode.ROTATE_0
ROTATE_0_FRAGMENT: StartMode  # value = rdkit.Chem.rdGaussianShape.StartMode.ROTATE_0_FRAGMENT
ROTATE_180: StartMode  # value = rdkit.Chem.rdGaussianShape.StartMode.ROTATE_180
ROTATE_180_FRAGMENT: StartMode  # value = rdkit.Chem.rdGaussianShape.StartMode.ROTATE_180_FRAGMENT
ROTATE_180_WIGGLE: StartMode  # value = rdkit.Chem.rdGaussianShape.StartMode.ROTATE_180_WIGGLE
ROTATE_45: StartMode  # value = rdkit.Chem.rdGaussianShape.StartMode.ROTATE_45
ROTATE_45_FRAGMENT: StartMode  # value = rdkit.Chem.rdGaussianShape.StartMode.ROTATE_45_FRAGMENT
SHAPE_ONLY: OptimMode  # value = rdkit.Chem.rdGaussianShape.OptimMode.SHAPE_ONLY
SHAPE_PLUS_COLOR: OptimMode  # value = rdkit.Chem.rdGaussianShape.OptimMode.SHAPE_PLUS_COLOR
SHAPE_PLUS_COLOR_SCORE: OptimMode  # value = rdkit.Chem.rdGaussianShape.OptimMode.SHAPE_PLUS_COLOR_SCORE
