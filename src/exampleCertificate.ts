import type { FormValues } from "./types";

export const exampleCertificateValues: FormValues = {
  issuerName: "John Doe",
  collectionName: "John Doe / Aster Vale Meteorite Archive",
  issuerEmail: "john.doe@example.org",
  issuerPhone: "+1 202 555 0147",
  issuerAddress: "100 Example Observatory Road, Flagstaff, AZ 86001, USA",
  issuerWebsite: "https://example.org/aster-vale",
  certificateId: "DEMO-AV-2026-0042",
  issueDate: "2026-07-29",
  certificateVersion: "1.0",
  certificateStatus: "active",
  certificateStyle: "museum-ledger",
  certificateTheme: "desert-copper",
  supersededCertificateId: "Not applicable - first active issue",
  certificateNotes: "Synthetic demonstration record created to show a fully populated Spacerocks COA.",
  meteoriteName: "Aster Vale 001 - DEMO",
  classification: "Synthetic L5 ordinary chondrite",
  weightGrams: "42.73",
  weightPrecision: "0.01",
  specimenForm: "Complete individual",
  dimensions: "46 x 35 x 28 mm",
  numberOfPieces: "1",
  preparationState: "As found; dry brushed only",
  identifyingMarks: "Dark fusion crust, four shallow regmaglypts, one exposed pale chondritic window",
  recordedOwner: "John Doe",
  fallStatus: "Desert find - synthetic example",
  fallDate: "2024-11-03",
  country: "Morocco (demonstration data)",
  region: "Draa-Tafilalet (demonstration data)",
  locality: "Aster Vale test locality",
  latitude: "30.0000 N (illustrative)",
  longitude: "5.0000 W (illustrative)",
  metbullCode: "DEMO-NOT-REGISTERED",
  officialReferenceUrl: "https://example.org/meteorites/aster-vale-001",
  recoveryInformation: "Fictional recovery narrative: found during a documented training survey and photographed in place before collection.",
  provenance: "Synthetic chain of custody from the fictional field team to the fictional Aster Vale archive; no real specimen or ownership claim is represented.",
  previousOwner: "Aster Vale training field team (fictional)",
  buyer: "Not transferred - demonstration entry",
  transferDate: "2026-07-29",
  invoiceReference: "DEMO-INV-0042",
  transferNotes: "No sale or transfer occurred; these values exist only to demonstrate a complete record.",
};

type ExampleField = {
  key: keyof FormValues;
  label: string;
};

export const exampleCertificateFieldGroups: readonly {
  title: string;
  fields: readonly ExampleField[];
}[] = [
  {
    title: "Issuer identity",
    fields: [
      { key: "issuerName", label: "Issuer name" },
      { key: "collectionName", label: "Collection or business" },
      { key: "issuerEmail", label: "Email" },
      { key: "issuerPhone", label: "Phone" },
      { key: "issuerAddress", label: "Address" },
      { key: "issuerWebsite", label: "Website" },
    ],
  },
  {
    title: "Certificate identity",
    fields: [
      { key: "certificateId", label: "Certificate ID" },
      { key: "issueDate", label: "Issue date" },
      { key: "certificateVersion", label: "Version" },
      { key: "certificateStatus", label: "Status" },
      { key: "certificateStyle", label: "Layout style" },
      { key: "certificateTheme", label: "Color scheme" },
      { key: "supersededCertificateId", label: "Superseded certificate ID" },
      { key: "certificateNotes", label: "Certificate notes" },
    ],
  },
  {
    title: "Specimen record",
    fields: [
      { key: "meteoriteName", label: "Meteorite name" },
      { key: "classification", label: "Classification" },
      { key: "weightGrams", label: "Weight (grams)" },
      { key: "weightPrecision", label: "Weight precision (grams)" },
      { key: "specimenForm", label: "Specimen form" },
      { key: "dimensions", label: "Dimensions" },
      { key: "numberOfPieces", label: "Number of pieces" },
      { key: "preparationState", label: "Preparation state" },
      { key: "identifyingMarks", label: "Identifying marks" },
      { key: "recordedOwner", label: "Recorded owner" },
    ],
  },
  {
    title: "Fall, find, and provenance",
    fields: [
      { key: "fallStatus", label: "Fall or find status" },
      { key: "fallDate", label: "Date" },
      { key: "country", label: "Country" },
      { key: "region", label: "Region" },
      { key: "locality", label: "Locality" },
      { key: "latitude", label: "Latitude" },
      { key: "longitude", label: "Longitude" },
      { key: "metbullCode", label: "Meteoritical Bulletin code" },
      { key: "officialReferenceUrl", label: "Official reference URL" },
      { key: "recoveryInformation", label: "Finder / recovery information" },
      { key: "provenance", label: "Provenance and chain of custody" },
      { key: "previousOwner", label: "Previous owner" },
      { key: "buyer", label: "Buyer / transferee" },
      { key: "transferDate", label: "Transfer date" },
      { key: "invoiceReference", label: "Invoice / reference" },
      { key: "transferNotes", label: "Transfer notes" },
    ],
  },
];
