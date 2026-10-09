from __future__ import annotations

from pathlib import Path

from kofvision.landxml_parser import parse_landxml


def test_parse_alignment_only_sample_file() -> None:
    path = Path("test data") / "Eksport_varelager_overflate261007 (1).xml"
    landxml = parse_landxml(str(path))

    assert landxml.namespace == "http://www.landxml.org/schema/LandXML-1.0"
    assert landxml.expected_schema_url == "http://www.landxml.org/schema/LandXML-1.0/LandXML-1.0.xsd"
    assert landxml.declared_schema_url == "http://www.landxml.org/schema/LandXML-1.0/LandXML-1.0.xsd"
    assert len(landxml.alignments) >= 1

    first_alignment = landxml.alignments[0]
    assert len(first_alignment.centerline) >= 2
    assert len(first_alignment.stations) >= 2


def test_parse_surface_points_and_faces(tmp_path: Path) -> None:
    xml_content = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<LandXML xmlns=\"http://www.landxml.org/schema/LandXML-1.2\" version=\"1.2\">
  <Surfaces>
    <Surface name=\"TopSurface\">
      <Definition surfType=\"TIN\">
        <Pnts>
          <P id=\"1\">0 0 10</P>
          <P id=\"2\">10 0 10</P>
          <P id=\"3\">10 10 10</P>
          <P id=\"4\">0 10 10</P>
        </Pnts>
        <Faces>
          <F>1 2 3</F>
          <F>1 3 4</F>
        </Faces>
      </Definition>
    </Surface>
  </Surfaces>
</LandXML>
"""
    path = tmp_path / "surface.xml"
    path.write_text(xml_content, encoding="utf-8")

    landxml = parse_landxml(str(path))
    assert len(landxml.surfaces) == 1

    surface = landxml.surfaces[0]
    assert surface.layer_name == "TopSurface"
    assert len(surface.all_points) == 4
    assert len(surface.faces) == 2

    bounds = surface.xy_bounds()
    assert bounds is not None
    assert bounds == (0.0, 0.0, 10.0, 10.0)


def test_strict_xsd_validation_with_local_schema_pass(tmp_path: Path) -> None:
    xml_content = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<LandXML xmlns=\"http://www.landxml.org/schema/LandXML-1.2\" version=\"1.2\">
  <Alignments />
</LandXML>
"""
    xsd_content = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<xsd:schema xmlns:xsd=\"http://www.w3.org/2001/XMLSchema\"
            targetNamespace=\"http://www.landxml.org/schema/LandXML-1.2\"
            xmlns=\"http://www.landxml.org/schema/LandXML-1.2\"
            elementFormDefault=\"qualified\">
  <xsd:element name=\"LandXML\">
    <xsd:complexType>
      <xsd:sequence>
        <xsd:any minOccurs=\"0\" maxOccurs=\"unbounded\" processContents=\"lax\"/>
      </xsd:sequence>
      <xsd:anyAttribute processContents=\"lax\"/>
    </xsd:complexType>
  </xsd:element>
</xsd:schema>
"""

    xml_path = tmp_path / "doc.xml"
    xsd_path = tmp_path / "landxml.xsd"
    xml_path.write_text(xml_content, encoding="utf-8")
    xsd_path.write_text(xsd_content, encoding="utf-8")

    doc = parse_landxml(
        str(xml_path),
        strict_xsd_validation=True,
        local_schema_path=str(xsd_path),
        allow_network_schema=False,
    )

    assert doc.xsd_validation_enabled is True
    assert doc.xsd_validation_mode == "local"
    assert doc.xsd_validation_passed is True
    assert doc.xsd_validation_errors == []


def test_strict_xsd_validation_with_local_schema_fail(tmp_path: Path) -> None:
    xml_content = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<LandXML xmlns=\"http://www.landxml.org/schema/LandXML-1.2\" version=\"1.2\">
  <Alignments />
</LandXML>
"""
    xsd_content = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<xsd:schema xmlns:xsd=\"http://www.w3.org/2001/XMLSchema\"
            targetNamespace=\"http://www.landxml.org/schema/LandXML-1.2\"
            xmlns=\"http://www.landxml.org/schema/LandXML-1.2\"
            elementFormDefault=\"qualified\">
  <xsd:element name=\"NotLandXML\" type=\"xsd:string\"/>
</xsd:schema>
"""

    xml_path = tmp_path / "doc.xml"
    xsd_path = tmp_path / "bad.xsd"
    xml_path.write_text(xml_content, encoding="utf-8")
    xsd_path.write_text(xsd_content, encoding="utf-8")

    doc = parse_landxml(
        str(xml_path),
        strict_xsd_validation=True,
        local_schema_path=str(xsd_path),
        allow_network_schema=False,
    )

    assert doc.xsd_validation_enabled is True
    assert doc.xsd_validation_mode == "local"
    assert doc.xsd_validation_passed is False
    assert len(doc.xsd_validation_errors) >= 1
