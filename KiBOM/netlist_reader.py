#
# KiCad python module for interpreting generic netlists which can be used
# to generate Bills of materials, etc.
#
# No string formatting is used on purpose as the only string formatting that
# is current compatible with python 2.4+ to 3.0+ is the '%' method, and that
# is due to be deprecated in 3.0+ soon
#

"""
    @package
    Generate a HTML BOM list.
    Components are sorted and grouped by value
    Any existing fields are read
"""


from __future__ import print_function
import sys
import xml.sax as sax
import re
import pdb

from component import Component, ComponentGroup
from sort import natural_sort

#-----<Configure>----------------------------------------------------------------

# excluded_fields is a list of regular expressions.  If any one matches a field
# from either a component or a libpart, then that will not be included as a
# column in the BOM.  Otherwise all columns from all used libparts and components
# will be unionized and will appear.  Some fields are impossible to blacklist, such
# as Ref, Value, Footprint, and Datasheet.  Additionally Qty and Item are supplied
# unconditionally as columns, and may not be removed.
excluded_fields = [
    #'Price@1000'
    ]


# You may exlude components from the BOM by either:
#
# 1) adding a custom field named "Installed" to your components and filling it
# with a value of "NU" (Normally Uninstalled).
# See netlist.getInterestingComponents(), or
#
# 2) blacklisting it in any of the three following lists:


# regular expressions which match component 'Reference' fields of components that
# are to be excluded from the BOM.
excluded_references = [
    'TP[0-9]+'              # all test points
    ]


# regular expressions which match component 'Value' fields of components that
# are to be excluded from the BOM.
excluded_values = [
    'MOUNTHOLE',
    'SCOPETEST',
    'MOUNT_HOLE',
    'SOLDER_BRIDGE.*'
    ]


# regular expressions which match component 'Footprint' fields of components that
# are to be excluded from the BOM.
excluded_footprints = [
    #'MOUNTHOLE'
    ]

# When comparing part names, components will match if they are both elements of the
# same set defined here
ALIASES = [
    ["c", "c_small", "cap", "capacitor"],
    ["r", "r_small", "res", "resistor"],
    ["sw", "switch"],
    ["l, l_small", "inductor"]
    ]

#-----</Configure>---------------------------------------------------------------

class xmlElement():
    """xml element which can represent all nodes of the netlist tree.  It can be
    used to easily generate various output formats by propogating format
    requests to children recursively.
    """
    def __init__(self, name, parent=None):
        self.name = name
        self.attributes = {}
        self.parent = parent
        self.chars = ""
        self.children = []

    def __str__(self):
        """String representation of this netlist element

        """
        return self.name + "[" + self.chars + "]" + " attr_count:" + str(len(self.attributes))

    def formatXML(self, nestLevel=0, amChild=False):
        """Return this element formatted as XML

        Keywords:
        nestLevel -- increases by one for each level of nesting.
        amChild -- If set to True, the start of document is not returned.

        """
        s = ""

        indent = ""
        for i in range(nestLevel):
            indent += "    "

        if not amChild:
            s = "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"

        s += indent + "<" + self.name
        for a in self.attributes:
            s += " " + a + "=\"" + self.attributes[a] + "\""

        if (len(self.chars) == 0) and (len(self.children) == 0):
            s += "/>"
        else:
            s += ">" + self.chars

        for c in self.children:
            s += "\n"
            s += c.formatXML(nestLevel+1, True)

        if (len(self.children) > 0):
            s += "\n" + indent

        if (len(self.children) > 0) or (len(self.chars) > 0):
            s += "</" + self.name + ">"

        return s

    def formatHTML(self, amChild=False):
        """Return this element formatted as HTML

        Keywords:
        amChild -- If set to True, the start of document is not returned

        """
        s = ""

        if not amChild:
            s = """<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN"
                "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
                <html xmlns="http://www.w3.org/1999/xhtml">
                <head>
                <meta http-equiv="Content-Type" content="text/html; charset=utf-8" />
                <title></title>
                </head>
                <body>
                <table>
                """

        s += "<tr><td><b>" + self.name + "</b><br>" + self.chars + "</td><td><ul>"
        for a in self.attributes:
            s += "<li>" + a + " = " + self.attributes[a] + "</li>"

        s += "</ul></td></tr>\n"

        for c in self.children:
            s += c.formatHTML(True)

        if not amChild:
            s += """</table>
                </body>
                </html>"""

        return s

    def addAttribute(self, attr, value):
        """Add an attribute to this element"""
        self.attributes[attr] = value

    def setAttribute(self, attr, value):
        """Set an attributes value - in fact does the same thing as add
        attribute

        """
        self.attributes[attr] = value

    def setChars(self, chars):
        """Set the characters for this element"""
        self.chars = chars

    def addChars(self, chars):
        """Add characters (textual value) to this element"""
        self.chars += chars

    def addChild(self, child):
        """Add a child element to this element"""
        self.children.append(child)
        return self.children[len(self.children) - 1]

    def getParent(self):
        """Get the parent of this element (Could be None)"""
        return self.parent

    def getChild(self, name):
        """Returns the first child element named 'name'

        Keywords:
        name -- The name of the child element to return"""
        for child in self.children:
            if child.name == name:
                return child
        return None

    def getChildren(self, name=None):
        if name:
            # return _all_ children named "name"
            ret = []
            for child in self.children:
                if child.name == name:
                    ret.append(child)
            return ret
        else:
            return self.children

    def get(self, elemName, attribute="", attrmatch=""):
        """Return the text data for either an attribute or an xmlElement
        """
        if (self.name == elemName):
            if attribute != "":
                try:
                    if attrmatch != "":
                        if self.attributes[attribute] == attrmatch:
                            return self.chars
                    else:
                        return self.attributes[attribute]
                except AttributeError:
                    return ""
            else:
                return self.chars

        for child in self.children:
            ret = child.get(elemName, attribute, attrmatch)
            if ret != "":
                return ret

        return ""

class libpart():
    """Class for a library part, aka 'libpart' in the xml netlist file.
    (Components in eeschema are instantiated from library parts.)
    This part class is implemented by wrapping an xmlElement with accessors.
    This xmlElement instance is held in field 'element'.
    """
    def __init__(self, xml_element):
        #
        self.element = xml_element

    #def __str__(self):
        # simply print the xmlElement associated with this part
        #return str(self.element)

    def getLibName(self):
        return self.element.get("libpart", "lib")

    def getPartName(self):
        return self.element.get("libpart", "part")

    def getDescription(self):
        return self.element.get("description")

    def getDocs(self):
        return self.element.get("docs")

    def getField(self, name):
        return self.element.get("field", "name", name)

    def getFieldNames(self):
        """Return a list of field names in play for this libpart.
        """
        fieldNames = []
        fields = self.element.getChild('fields')
        if fields:
            for f in fields.getChildren():
                fieldNames.append( f.get('field','name') )
        return fieldNames

    def getDatasheet(self):

        datasheet = self.getField("Datasheet")

        if not datasheet or datasheet == "":
            docs = self.getDocs()

            if "http" in docs or ".pdf" in docs:
                datasheet = docs

        return datasheet

    def getFootprint(self):
        return self.getField("Footprint")

    def getAliases(self):
        """Return a list of aliases or None"""
        aliases = self.element.getChild("aliases")
        if aliases:
            ret = []
            children = aliases.getChildren()
            # grab the text out of each child:
            for child in children:
                ret.append( child.get("alias") )
            return ret
        return None


class netlist():
    """ Kicad generic netlist class. Generally loaded from a kicad generic
    netlist file. Includes several helper functions to ease BOM creating
    scripts

    """
    def __init__(self, fname=""):
        """Initialiser for the genericNetlist class

        Keywords:
        fname -- The name of the generic netlist file to open (Optional)

        """
        self.design = None
        self.components = []
        self.libparts = []
        self.libraries = []
        self.nets = []

        # The entire tree is loaded into self.tree
        self.tree = []

        self._curr_element = None

        # component blacklist regexs, made from exluded_* above.
        self.excluded_references = []
        self.excluded_values = []
        self.excluded_footprints = []

        if fname != "":
            self.load(fname)

    def addChars(self, content):
        """Add characters to the current element"""
        self._curr_element.addChars(content)

    def addElement(self, name):
        """Add a new kicad generic element to the list"""
        if self._curr_element == None:
            self.tree = xmlElement(name)
            self._curr_element = self.tree
        else:
            self._curr_element = self._curr_element.addChild(
                xmlElement(name, self._curr_element))

        # If this element is a component, add it to the components list
        if self._curr_element.name == "comp":
            self.components.append(Component(self._curr_element))

        # Assign the design element
        if self._curr_element.name == "design":
            self.design = self._curr_element

        # If this element is a library part, add it to the parts list
        if self._curr_element.name == "libpart":
            self.libparts.append(libpart(self._curr_element))

        # If this element is a net, add it to the nets list
        if self._curr_element.name == "net":
            self.nets.append(self._curr_element)

        # If this element is a library, add it to the libraries list
        if self._curr_element.name == "library":
            self.libraries.append(self._curr_element)

        return self._curr_element

    def endDocument(self):
        """Called when the netlist document has been fully parsed"""
        # When the document is complete, the library parts must be linked to
        # the components as they are seperate in the tree so as not to
        # duplicate library part information for every component
        for c in self.components:
            for p in self.libparts:
                if p.getLibName() == c.getLibName():
                    if p.getPartName() == c.getPartName():
                        c.setLibPart(p)
                        break
                    else:
                        aliases = p.getAliases()
                        if aliases and self.aliasMatch( c.getPartName(), aliases ):
                            c.setLibPart(p)
                            break;

            if not c.getLibPart():
                print( 'missing libpart for ref:', c.getRef(), c.getPartName(), c.getLibName() )


    def aliasMatch(self, partName, aliasList):
        for alias in aliasList:
            if partName == alias:
                return True
        return False

    def endElement(self):
        """End the current element and switch to its parent"""
        self._curr_element = self._curr_element.getParent()

    def getDate(self):
        """Return the date + time string generated by the tree creation tool"""
        return self.design.get("date")

    def getSource(self):
        """Return the source string for the design"""
        return self.design.get("source")

    def getTool(self):
        """Return the tool string which was used to create the netlist tree"""
        return self.design.get("tool")
        
    def getSheet(self):
        return self.design.getChild("sheet")
        
    def getSheetDate(self):
        sheet= self.getSheet()
        if sheet == None: return ""
        return sheet.get("date")
        
    def getVersion(self):
        """Return the verison of the sheet info"""
        sheet = self.getSheet()
        if sheet == None: return ""
        return sheet.get("rev")

    def getInterestingComponents(self):
        """Return a subset of all components, those that should show up in the BOM.
        Omit those that should not, by consulting the blacklists:
        excluded_values, excluded_refs, and excluded_footprints, which hold one
        or more regular expressions.  If any of the the regular expressions match
        the corresponding field's value in a component, then the component is exluded.
        """

        # pre-compile all the regex expressions:
        del self.excluded_references[:]
        del self.excluded_values[:]
        del self.excluded_footprints[:]

        for rex in excluded_references:
            self.excluded_references.append( re.compile( rex ) )

        for rex in excluded_values:
            self.excluded_values.append( re.compile( rex ) )

        for rex in excluded_footprints:
            self.excluded_footprints.append( re.compile( rex ) )

        # the subset of components to return, considered as "interesting".
        ret = []

        # run each component thru a series of tests, if it passes all, then add it
        # to the interesting list 'ret'.
        for c in self.components:
            exclude = False
            if not exclude:
                for refs in self.excluded_references:
                    if refs.match(c.getRef()):
                        exclude = True
                        break;
            if not exclude:
                for vals in self.excluded_values:
                    if vals.match(c.getValue()):
                        exclude = True
                        break;
            if not exclude:
                for mods in self.excluded_footprints:
                    if mods.match(c.getFootprint()):
                        exclude = True
                        break;

            if not exclude:
                # This is a fairly personal way to flag DNS (Do Not Stuff).  NU for
                # me means Normally Uninstalled.  You can 'or in' another expression here.
                if c.getField( "Installed" ) == 'NU':
                    exclude = True

            if not exclude:
                ret.append(c)

        # Sort first by ref as this makes for easier to read BOM's
        ret.sort(key=lambda g: g.getRef())

        return ret


    def groupComponents(self, components = None):
        """Return a list of component lists. Components are grouped together
        when the value, library and part identifiers match.
		
		ALSO THE FOOTPRINTS MUST MATCH YOU DINGBAT

        Keywords:
        components -- is a list of components, typically an interesting subset
        of all components, or None.  If None, then all components are looked at.
        """
        if not components:
            components = self.components

        groups = []
        
        """
        Iterate through each component, and test whether a group for these already exists
        """
        for c in components:
            found = False
            
            for g in groups:
                if g.matchComponent(c):
                    g.addComponent(c)
                    found = True
                    break
            
            if not found:
                g = ComponentGroup()
                g.addComponent(c)
                groups.append(g)
            
        #sort the references within each group
        for g in groups:
            g.sortComponents()
            g.updateFields()

        #sort the groups
        #first priority is the Type of component (e.g. R?, U?, L?)
        groups = sorted(groups, key=lambda g: [g.components[0].getPrefix(), g.components[0].getValue()])
                
        return groups

    def formatXML(self):
        """Return the whole netlist formatted in XML"""
        return self.tree.formatXML()

    def formatHTML(self):
        """Return the whole netlist formatted in HTML"""
        return self.tree.formatHTML()

    def load(self, fname):
        """Load a kicad generic netlist

        Keywords:
        fname -- The name of the generic netlist file to open

        """
        try:
            self._reader = sax.make_parser()
            self._reader.setContentHandler(_gNetReader(self))
            self._reader.parse(fname)
        except IOError as e:
            print( __file__, ":", e, file=sys.stderr )
            sys.exit(-1)



class _gNetReader(sax.handler.ContentHandler):
    """SAX kicad generic netlist content handler - passes most of the work back
    to the 'netlist' class which builds a complete tree in RAM for the design

    """
    def __init__(self, aParent):
        self.parent = aParent

    def startElement(self, name, attrs):
        """Start of a new XML element event"""
        element = self.parent.addElement(name)

        for name in attrs.getNames():
            element.addAttribute(name, attrs.getValue(name))

    def endElement(self, name):
        self.parent.endElement()

    def characters(self, content):
        # Ignore erroneous white space - ignoreableWhitespace does not get rid
        # of the need for this!
        if not content.isspace():
            self.parent.addChars(content)

    def endDocument(self):
        """End of the XML document event"""
        self.parent.endDocument()
