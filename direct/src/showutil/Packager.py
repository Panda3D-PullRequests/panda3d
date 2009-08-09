""" This module is used to build a "Package", a collection of files
within a Panda3D Multifile, which can be easily be downloaded and/or
patched onto a client machine, for the purpose of running a large
application. """

import sys
import os
import glob
import marshal
import new
from direct.showbase import Loader
from direct.showutil import FreezeTool
from direct.directnotify.DirectNotifyGlobal import *
from pandac.PandaModules import *

vfs = VirtualFileSystem.getGlobalPtr()

class PackagerError(StandardError):
    pass

class OutsideOfPackageError(PackagerError):
    pass

class ArgumentError(PackagerError):
    pass

class Packager:
    notify = directNotify.newCategory("Packager")

    class PackFile:
        def __init__(self, filename, newName = None, deleteTemp = False):
            assert isinstance(filename, Filename)
            self.filename = filename
            self.newName = newName
            self.deleteTemp = deleteTemp

    class Package:
        def __init__(self, packageName, packager):
            self.packageName = packageName
            self.packager = packager
            self.version = None
            self.platform = None
            self.p3dApplication = False
            self.files = []
            self.compressionLevel = 0
            self.importedMapsDir = 'imported_maps'

            # This is the set of files and modules, already included
            # by required packages, that we can skip.
            self.skipFilenames = {}
            self.skipModules = {}

            # This records the current list of modules we have added so
            # far.
            self.freezer = FreezeTool.Freezer()

            # Set this true to parse and build up the internal
            # filelist, but not generate any output.
            self.dryRun = False

        def close(self):
            """ Writes out the contents of the current package. """

            self.packageBasename = self.packageName
            packageDir = self.packageName
            if self.platform:
                self.packageBasename += '_' + self.platform
                packageDir += '/' + self.platform
            if self.version:
                self.packageBasename += '_' + self.version
                packageDir += '/' + self.version

            self.packageDesc = self.packageBasename + '.xml'
            self.packageImportDesc = self.packageBasename + '_import.xml'
            if self.p3dApplication:
                self.packageBasename += '.p3d'
                packageDir = ''
            else:
                self.packageBasename += '.mf'
                packageDir += '/'

            self.packageFilename = packageDir + self.packageBasename
            self.packageDesc = packageDir + self.packageDesc
            self.packageImportDesc = packageDir + self.packageImportDesc

            Filename(self.packageFilename).makeDir()

            try:
                os.unlink(self.packageFilename)
            except OSError:
                pass

            if self.dryRun:
                self.multifile = None
            else:
                self.multifile = Multifile()
                self.multifile.openReadWrite(self.packageFilename)

            self.extracts = []
            self.components = []

            # Exclude modules already imported in a required package.
            for moduleName in self.skipModules.keys():
                self.freezer.excludeModule(moduleName)

            # Build up a cross-reference of files we've already
            # discovered.
            self.sourceFilenames = {}
            self.targetFilenames = {}
            processFiles = []
            for file in self.files:
                if not file.newName:
                    file.newName = file.filename
                if file.newName in self.skipFilenames:
                    # Skip this file.
                    continue

                # Convert the source filename to an unambiguous
                # filename for searching.
                filename = Filename(file.filename)
                filename.makeCanonical()
                
                self.sourceFilenames[filename] = file
                self.targetFilenames[file.newName] = file
                processFiles.append(file)

            for file in processFiles:
                ext = file.filename.getExtension()
                if ext == 'py':
                    self.addPyFile(file)
                elif not self.dryRun:
                    if ext == 'pz':
                        # Strip off an implicit .pz extension.
                        filename = Filename(file.filename)
                        filename.setExtension('')
                        filename = Filename(filename.cStr())
                        ext = filename.getExtension()

                        filename = Filename(file.newName)
                        if filename.getExtension() == 'pz':
                            filename.setExtension('')
                            file.newName = filename.cStr()

                    if ext == 'egg':
                        self.addEggFile(file)
                    elif ext == 'bam':
                        self.addBamFile(file)
                    elif ext in self.packager.imageExtensions:
                        self.addTexture(file)
                    else:
                        # Any other file.
                        self.addComponent(file)

            # Pick up any unfrozen Python files.
            self.freezer.done()

            # Add known module names.
            self.moduleNames = {}
            for moduleName in self.freezer.getAllModuleNames():
                self.moduleNames[moduleName] = True

                xmodule = TiXmlElement('module')
                xmodule.SetAttribute('name', moduleName)
                self.components.append(xmodule)

            if not self.dryRun:
                self.freezer.addToMultifile(self.multifile)
                self.multifile.repack()
                self.multifile.close()

                if not self.p3dApplication:
                    self.compressMultifile()
                    self.writeDescFile()
                    self.writeImportDescFile()

            # Now that all the files have been packed, we can delete
            # the temporary files.
            for file in self.files:
                if file.deleteTemp:
                    file.filename.unlink()

        def compressMultifile(self):
            """ Compresses the .mf file into an .mf.pz file. """

            compressedName = self.packageFilename + '.pz'
            if not compressFile(self.packageFilename, compressedName, 6):
                message = 'Unable to write %s' % (compressedName)
                raise PackagerError, message

        def writeDescFile(self):
            doc = TiXmlDocument(self.packageDesc)
            decl = TiXmlDeclaration("1.0", "utf-8", "")
            doc.InsertEndChild(decl)

            xpackage = TiXmlElement('package')
            xpackage.SetAttribute('name', self.packageName)
            if self.platform:
                xpackage.SetAttribute('platform', self.platform)
            if self.version:
                xpackage.SetAttribute('version', self.version)

            xuncompressedArchive = self.getFileSpec(
                'uncompressed_archive', self.packageFilename, self.packageBasename)
            xcompressedArchive = self.getFileSpec(
                'compressed_archive', self.packageFilename + '.pz', self.packageBasename + '.pz')
            xpackage.InsertEndChild(xuncompressedArchive)
            xpackage.InsertEndChild(xcompressedArchive)

            for xextract in self.extracts:
                xpackage.InsertEndChild(xextract)

            doc.InsertEndChild(xpackage)
            doc.SaveFile()

        def writeImportDescFile(self):
            doc = TiXmlDocument(self.packageImportDesc)
            decl = TiXmlDeclaration("1.0", "utf-8", "")
            doc.InsertEndChild(decl)

            xpackage = TiXmlElement('package')
            xpackage.SetAttribute('name', self.packageName)
            if self.platform:
                xpackage.SetAttribute('platform', self.platform)
            if self.version:
                xpackage.SetAttribute('version', self.version)

            for xcomponent in self.components:
                xpackage.InsertEndChild(xcomponent)

            doc.InsertEndChild(xpackage)
            doc.SaveFile()


        def getFileSpec(self, element, filename, newName):
            """ Returns an xcomponent or similar element with the file
            information for the indicated file. """
            
            xspec = TiXmlElement(element)

            filename = Filename(filename)
            size = filename.getFileSize()
            timestamp = filename.getTimestamp()

            hv = HashVal()
            hv.hashFile(filename)
            hash = hv.asHex()

            xspec.SetAttribute('filename', newName)
            xspec.SetAttribute('size', str(size))
            xspec.SetAttribute('timestamp', str(timestamp))
            xspec.SetAttribute('hash', hash)

            return xspec

            

        def addPyFile(self, file):
            """ Adds the indicated python file, identified by filename
            instead of by module name, to the package. """

            # Convert the raw filename back to a module name, so we
            # can see if we've already loaded this file.  We assume
            # that all Python files within the package will be rooted
            # at the top of the package.

            filename = file.newName.rsplit('.', 1)[0]
            moduleName = filename.replace("/", ".")
            if moduleName.endswith('.__init__'):
                moduleName = moduleName.rsplit('.', 1)[0]

            if moduleName in self.freezer.modules:
                # This Python file is already known.  We don't have to
                # deal with it again.
                return

            self.freezer.addModule(moduleName, newName = moduleName,
                                   filename = file.filename)

        def addEggFile(self, file):
            # Precompile egg files to bam's.
            np = self.packager.loader.loadModel(file.filename, okMissing = True)
            if not np:
                raise StandardError, 'Could not read egg file %s' % (file.filename)

            bamName = Filename(file.newName)
            bamName.setExtension('bam')
            self.addNode(np.node(), file.filename, bamName.cStr())

        def addBamFile(self, file):
            # Load the bam file so we can massage its textures.
            bamFile = BamFile()
            if not bamFile.openRead(file.filename):
                raise StandardError, 'Could not read bam file %s' % (file.filename)

            if not bamFile.resolve():
                raise StandardError, 'Could not resolve bam file %s' % (file.filename)

            node = bamFile.readNode()
            if not node:
                raise StandardError, 'Not a model file: %s' % (file.filename)

            self.addNode(node, file.filename, file.newName)

        def addNode(self, node, filename, newName):
            """ Converts the indicated node to a bam stream, and adds the
            bam file to the multifile under the indicated newName. """

            # If the Multifile already has a file by this name, don't
            # bother adding it again.
            if self.multifile.findSubfile(newName) >= 0:
                return

            # Be sure to import all of the referenced textures, and tell
            # them their new location within the multifile.

            for tex in NodePath(node).findAllTextures():
                if not tex.hasFullpath() and tex.hasRamImage():
                    # We need to store this texture as a raw-data image.
                    # Clear the newName so this will happen
                    # automatically.
                    tex.clearFilename()
                    tex.clearAlphaFilename()

                else:
                    # We can store this texture as a file reference to its
                    # image.  Copy the file into our multifile, and rename
                    # its reference in the texture.
                    if tex.hasFilename():
                        tex.setFilename(self.addFoundTexture(tex.getFullpath()))
                    if tex.hasAlphaFilename():
                        tex.setAlphaFilename(self.addFoundTexture(tex.getAlphaFullpath()))

            # Now generate an in-memory bam file.  Tell the bam writer to
            # keep the textures referenced by their in-multifile path.
            bamFile = BamFile()
            stream = StringStream()
            bamFile.openWrite(stream)
            bamFile.getWriter().setFileTextureMode(bamFile.BTMUnchanged)
            bamFile.writeObject(node)
            bamFile.close()

            # Clean the node out of memory.
            node.removeAllChildren()

            # Now we have an in-memory bam file.
            stream.seekg(0)
            self.multifile.addSubfile(newName, stream, self.compressionLevel)

            # Flush it so the data gets written to disk immediately, so we
            # don't have to keep it around in ram.
            self.multifile.flush()
            
            xcomponent = TiXmlElement('component')
            xcomponent.SetAttribute('filename', newName)
            self.components.append(xcomponent)

        def addFoundTexture(self, filename):
            """ Adds the newly-discovered texture to the output, if it has
            not already been included.  Returns the new name within the
            package tree. """

            assert not filename.isLocal()

            filename = Filename(filename)
            filename.makeCanonical()

            file = self.sourceFilenames.get(filename, None)
            if file:
                # Never mind, it's already on the list.
                return file.newName

            # We have to copy the image into the plugin tree somewhere.
            newName = self.importedMapsDir + '/' + filename.getBasename()
            uniqueId = 0
            while newName in self.targetFilenames:
                uniqueId += 1
                newName = '%s/%s_%s.%s' % (
                    self.importedMapsDir, filename.getBasenameWoExtension(),
                    uniqueId, filename.getExtension())

            file = Packager.PackFile(filename, newName = newName)
            self.sourceFilenames[filename] = file
            self.targetFilenames[newName] = file
            self.addTexture(file)

            return newName

        def addTexture(self, file):
            """ Adds a texture image to the output. """

            if self.multifile.findSubfile(file.newName) >= 0:
                # Already have this texture.
                return

            # Texture file formats are generally already compressed and
            # not further compressible.
            self.addComponent(file, compressible = False)

        def addComponent(self, file, compressible = True, extract = False):
            ext = Filename(file.newName).getExtension()
            if ext in self.packager.uncompressibleExtensions:
                compressible = False
            if ext in self.packager.extractExtensions:
                extract = True
                
            compressionLevel = 0
            if compressible:
                compressionLevel = self.compressionLevel
                
            self.multifile.addSubfile(file.newName, file.filename, compressionLevel)
            if extract:
                xextract = self.getFileSpec('extract', file.filename, file.newName)
                self.extracts.append(xextract)

            xcomponent = TiXmlElement('component')
            xcomponent.SetAttribute('filename', file.newName)
            self.components.append(xcomponent)

    def __init__(self):

        # The following are config settings that the caller may adjust
        # before calling any of the command methods.

        # These should each be a Filename, or None if they are not
        # filled in.
        self.installDir = None
        self.persistDir = None

        # The platform string.
        self.platform = PandaSystem.getPlatform()

        # Optional signing and encrypting features.
        self.encryptionKey = None
        self.prcEncryptionKey = None
        self.prcSignCommand = None

        # This is a list of filename extensions and/or basenames that
        # indicate files that should be encrypted within the
        # multifile.  This provides obfuscation only, not real
        # security, since the decryption key must be part of the
        # client and is therefore readily available to any hacker.
        # Not only is this feature useless, but using it also
        # increases the size of your patchfiles, since encrypted files
        # don't patch as tightly as unencrypted files.  But it's here
        # if you really want it.
        self.encryptExtensions = ['ptf', 'dna', 'txt', 'dc']
        self.encryptFiles = []

        # This is the list of DC import suffixes that should be
        # available to the client.  Other suffixes, like AI and UD,
        # are server-side only and should be ignored by the Scrubber.
        self.dcClientSuffixes = ['OV']

        # Get the list of filename extensions that are recognized as
        # image files.
        self.imageExtensions = []
        for type in PNMFileTypeRegistry.getGlobalPtr().getTypes():
            self.imageExtensions += type.getExtensions()

        # Other useful extensions.  The .pz extension is implicitly
        # stripped.

        # Model files.
        self.modelExtensions = [ 'egg', 'bam' ]

        # Text files that are copied (and compressed) to the package
        # without processing.
        self.textExtensions = [ 'prc', 'ptf', 'txt' ]

        # Binary files that are copied (and compressed) without
        # processing.
        self.binaryExtensions = [ 'ttf', 'wav', 'mid' ]

        # Files that should be extracted to disk.
        self.extractExtensions = [ 'dll', 'so', 'dylib', 'exe' ]

        # Binary files that are considered uncompressible, and are
        # copied without compression.
        self.uncompressibleExtensions = [ 'mp3', 'ogg' ]

        # A Loader for loading models.
        self.loader = Loader.Loader(self)
        self.sfxManagerList = None
        self.musicManager = None

        # This is filled in during readPackageDef().
        self.packageList = None

        # A table of all known packages by name.
        self.packages = {}

        self.dryRun = False


    def setup(self):
        """ Call this method to initialize the class after filling in
        some of the values in the constructor. """

        self.knownExtensions = self.imageExtensions + self.modelExtensions + self.textExtensions + self.binaryExtensions + self.uncompressibleExtensions

        self.currentPackage = None

        # The persist dir is the directory in which the results from
        # past publishes are stored so we can generate patches against
        # them.  There must be a nonempty directory name here.
        assert(self.persistDir)

        # If the persist dir names an empty or nonexistent directory,
        # we will be generating a brand new publish with no previous
        # patches.
        self.persistDir.makeDir()

        # Within the persist dir, we make a temporary holding dir for
        # generating multifiles.
        self.mfTempDir = Filename(self.persistDir, Filename('mftemp/'))
        #self.mfTempDir.makeDir()

        # We also need a temporary holding dir for squeezing py files.
        self.pyzTempDir = Filename(self.persistDir, Filename('pyz/'))
        #self.pyzTempDir.makeDir()

        # Change to the persist directory so the temp files will be
        # created there
        os.chdir(self.persistDir.toOsSpecific())

    def readPackageDef(self, packageDef):
        """ Reads the lines in the .pdef file named by packageDef and
        dispatches to the appropriate handler method for each
        line.  Returns the list of package files."""

        assert self.packageList is None
        self.packageList = []

        self.notify.info('Reading %s' % (packageDef))
        file = open(packageDef.toOsSpecific())
        lines = file.readlines()
        file.close()

        lineNum = [0]
        def getNextLine(lineNum = lineNum):
            """
            Read in the next line of the packageDef
            """
            while lineNum[0] < len(lines):
                line = lines[lineNum[0]].strip()
                lineNum[0] += 1
                if not line:
                    # Skip the line, it was just a blank line
                    pass
                elif line[0] == '#':
                    # Eat python-style comments.
                    pass
                else:
                    # Remove any trailing comment.
                    hash = line.find(' #')
                    if hash != -1:
                        line = line[:hash].strip()
                    # Return the line as an array split at whitespace.
                    return line.split()

            # EOF.
            return None

        # Now start parsing the packageDef lines
        try:
            lineList = getNextLine()
            while lineList:
                command = lineList[0]
                try:
                    methodName = 'parse_%s' % (command)
                    method = getattr(self, methodName, None)
                    if method:
                        method(lineList)

                    else:
                        message = 'Unknown command %s' % (command)
                        raise PackagerError, message
                except ArgumentError:
                    message = 'Wrong number of arguments for command %s' %(command)
                    raise ArgumentError, message
                except OutsideOfPackageError:
                    message = '%s command encounted outside of package specification' %(command)
                    raise OutsideOfPackageError, message

                lineList = getNextLine()

        except PackagerError:
            # Append the line number and file name to the exception
            # error message.
            inst = sys.exc_info()[1]
            inst.args = (inst.args[0] + ' on line %s of %s' % (lineNum[0], packageDef),)
            raise

        packageList = self.packageList
        self.packageList = None

        return packageList

    def parse_setenv(self, lineList):
        """
        setenv variable value
        """
        
        try:
            command, variable, value = lineList
        except ValueError:
            raise ArgumentNumber

        value = ExecutionEnvironment.expandString(value)
        ExecutionEnvironment.setEnvironmentVariable(variable, value)

    def parse_begin_package(self, lineList):
        """
        begin_package packageName
        """
        
        try:
            command, packageName = lineList
        except ValueError:
            raise ArgumentNumber

        self.beginPackage(packageName)

    def parse_end_package(self, lineList):
        """
        end_package packageName
        """

        try:
            command, packageName = lineList
        except ValueError:
            raise ArgumentError

        self.endPackage(packageName)

    def parse_require(self, lineList):
        """
        require packageName
        """

        try:
            command, packageName = lineList
        except ValueError:
            raise ArgumentError

        package = self.findPackage(packageName)
        if not package:
            message = "Unknown package %s" % (packageName)
            raise PackagerError, message

        self.require(package)

    def parse_module(self, lineList):
        """
        module moduleName [newName]
        """
        newName = None

        try:
            if len(lineList) == 2:
                command, moduleName = lineList
            else:
                command, moduleName, newName = lineList
        except ValueError:
            raise ArgumentError

        self.module(moduleName, newName = newName)

    def parse_freeze_exe(self, lineList):
        """
        freeze_exe path/to/basename
        """

        try:
            command, filename = lineList
        except ValueError:
            raise ArgumentError

        self.freeze(filename, compileToExe = True)

    def parse_freeze_dll(self, lineList):
        """
        freeze_dll path/to/basename
        """

        try:
            command, filename = lineList
        except ValueError:
            raise ArgumentError

        self.freeze(filename, compileToExe = False)

    def parse_file(self, lineList):
        """
        file filename [newNameOrDir]
        """

        newNameOrDir = None

        try:
            if len(lineList) == 2:
                command, filename = lineList
            else:
                command, filename, newNameOrDir = lineList
        except ValueError:
            raise ArgumentError

        self.file(filename, newNameOrDir = newNameOrDir)

    def parse_dir(self, lineList):
        """
        dir dirname [newDir]
        """

        newDir = None

        try:
            if len(lineList) == 2:
                command, dirname = lineList
            else:
                command, dirname, newDir = lineList
        except ValueError:
            raise ArgumentError

        self.dir(dirname, newDir = newDir)
    
    def beginPackage(self, packageName):
        """ Begins a new package specification.  packageName is the
        basename of the package.  Follow this with a number of calls
        to file() etc., and close the package with endPackage(). """

        if self.currentPackage:
            raise PackagerError, 'unmatched end_package %s' % (self.currentPackage.packageName)

        package = self.Package(packageName, self)
        package.dryRun = self.dryRun
        
        self.currentPackage = package

    def endPackage(self, packageName):
        """ Closes a package specification.  This actually generates
        the package file.  The packageName must match the previous
        call to beginPackage(). """
        
        if not self.currentPackage:
            raise PackagerError, 'unmatched end_package %s' % (packageName)
        if self.currentPackage.packageName != packageName:
            raise PackagerError, 'end_package %s where %s expected' % (
                packageName, self.currentPackage.packageName)

        package = self.currentPackage
        package.close()

        self.packageList.append(package)
        self.packages[package.packageName] = package
        self.currentPackage = None

    def findPackage(self, packageName, searchUrl = None):
        """ Searches for the named package from a previous publish
        operation, either at the indicated URL or along the default
        search path.

        Returns the Package object, or None if the package cannot be
        located. """

        # Is it a package we already have resident?
        package = self.packages.get(packageName, None)
        if package:
            return package

        return None

    def require(self, package):
        """ Indicates a dependency on the indicated package.

        Attempts to install this package will implicitly install the
        named package also.  Files already included in the named
        package will be omitted from this one. """

        for filename in package.targetFilenames.keys():
            self.currentPackage.skipFilenames[filename] = True
        for moduleName in package.moduleNames.keys():
            self.currentPackage.skipModules[moduleName] = True

    def module(self, moduleName, newName = None):
        """ Adds the indicated Python module to the current package. """

        if not self.currentPackage:
            raise OutsideOfPackageError

        self.currentPackage.freezer.addModule(moduleName, newName = newName)

    def freeze(self, filename, compileToExe = False):
        """ Freezes all of the current Python code into either an
        executable (if compileToExe is true) or a dynamic library (if
        it is false).  The resulting compiled binary is added to the
        current package under the indicated filename.  The filename
        should not include an extension; that will be added. """

        if not self.currentPackage:
            raise OutsideOfPackageError

        package = self.currentPackage
        freezer = package.freezer
        freezer.done()

        if not package.dryRun:
            dirname = ''
            basename = filename
            if '/' in basename:
                dirname, basename = filename.rsplit('/', 1)
                dirname += '/'
            basename = freezer.generateCode(basename, compileToExe = compileToExe)

            package.files.append(self.PackFile(Filename(basename), newName = dirname + basename, deleteTemp = True))

        # Reset the freezer for more Python files.
        freezer.reset()


    def file(self, filename, newNameOrDir = None):
        """ Adds the indicated arbitrary file to the current package.

        The file is placed in the named directory, or the toplevel
        directory if no directory is specified.

        The filename may include environment variable references and
        shell globbing characters.

        Certain special behavior is invoked based on the filename
        extension.  For instance, .py files may be automatically
        compiled and stored as Python modules.

        If newNameOrDir ends in a slash character, it specifies the
        directory in which the file should be placed.  In this case,
        all files matched by the filename expression are placed in the
        named directory.

        If newNameOrDir ends in something other than a slash
        character, it specifies a new filename.  In this case, the
        filename expression must match only one file.

        If newNameOrDir is unspecified or None, the file is placed in
        the toplevel directory, regardless of its source directory.
        
        """

        if not self.currentPackage:
            raise OutsideOfPackageError

        expanded = Filename.expandFrom(filename)
        files = glob.glob(expanded.toOsSpecific())
        if not files:
            self.notify.warning("No such file: %s" % (expanded))
            return

        newName = None
        prefix = ''
        
        if newNameOrDir:
            if newNameOrDir[-1] == '/':
                prefix = newNameOrDir
            else:
                newName = newNameOrDir
                if len(files) != 1:
                    message = 'Cannot install multiple files on target filename %s' % (newName)
                    raise PackagerError, message

        for filename in files:
            filename = Filename.fromOsSpecific(filename)
            basename = filename.getBasename()
            if newName:
                self.addFile(filename, newName = newName)
            else:
                self.addFile(filename, newName = prefix + basename)

    def dir(self, dirname, newDir = None):

        """ Adds the indicated directory hierarchy to the current
        package.  The directory hierarchy is walked recursively, and
        all files that match a known extension are added to the package.

        The dirname may include environment variable references.

        newDir specifies the directory name within the package which
        the contents of the named directory should be installed to.
        If it is omitted, the contents of the named directory are
        installed to the root of the package.
        """

        if not self.currentPackage:
            raise OutsideOfPackageError

        dirname = Filename.expandFrom(dirname)
        if not newDir:
            newDir = ''

        self.__recurseDir(dirname, newDir)

    def __recurseDir(self, filename, newName):
        dirList = vfs.scanDirectory(filename)
        if dirList:
            # It's a directory name.  Recurse.
            prefix = newName
            if prefix and prefix[-1] != '/':
                prefix += '/'
            for subfile in dirList:
                filename = subfile.getFilename()
                self.__recurseDir(filename, prefix + filename.getBasename())
            return

        # It's a file name.  Add it.
        ext = filename.getExtension()
        if ext == 'py':
            self.addFile(filename, newName = newName)
        else:
            if ext == 'pz':
                # Strip off an implicit .pz extension.
                newFilename = Filename(filename)
                newFilename.setExtension('')
                newFilename = Filename(newFilename.cStr())
                ext = newFilename.getExtension()

            if ext in self.knownExtensions:
                self.addFile(filename, newName = newName)

    def addFile(self, filename, newName = None):
        """ Adds the named file, giving it the indicated name within
        the package. """

        if not self.currentPackage:
            raise OutsideOfPackageError

        self.currentPackage.files.append(
            self.PackFile(filename, newName = newName))
