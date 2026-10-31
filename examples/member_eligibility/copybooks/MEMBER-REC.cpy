      *----------------------------------------------------------------*
      * MEMBER MASTER EXTRACT - SYNTHETIC EXAMPLE FOR MAPVERITY        *
      * RECORD LENGTH 100, EBCDIC (CP037), FIXED BLOCK                 *
      *----------------------------------------------------------------*
       01  MEMBER-REC.
           05  MBR-ID                  PIC X(10).
           05  MBR-NAME.
               10  MBR-FIRST-NAME      PIC X(15).
               10  MBR-LAST-NAME       PIC X(20).
           05  MBR-DOB                 PIC 9(8).
           05  MBR-GENDER-CD           PIC X(1).
               88  MBR-MALE            VALUE 'M'.
               88  MBR-FEMALE          VALUE 'F'.
           05  MBR-COUNTY-CD           PIC X(2).
           05  MBR-HH-SIZE             PIC 9(2).
           05  MBR-MONTHLY-INCOME      PIC S9(7)V99 COMP-3.
           05  MBR-STATUS-CD           PIC X(1).
           05  MBR-PHONE               PIC X(10).
           05  MBR-LAST-UPDT           PIC 9(8).
           05  FILLER                  PIC X(18).
