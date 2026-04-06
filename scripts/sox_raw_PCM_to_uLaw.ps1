# Create an output directory if it doesn't exist
$outputDir = "converted"
if (!(Test-Path $outputDir)) { New-Item -ItemType Directory -Path $outputDir }

# Get all .wav files in the current directory
$wavFiles = Get-ChildItem -Filter *.wav

foreach ($file in $wavFiles) {
    # Define the output file path
    $outputFile = Join-Path $outputDir $file.Name
    
    Write-Host "Converting: $($file.Name)..."
    
    # SoX conversion command:
    # -r 8000: Sets sample rate to 8kHz
    # -c 1: Sets channels to 1 (mono)
    # -b 8: Sets bit depth to 8-bit
    # -e u-law: Sets encoding to u-law
    sox "$($file.FullName)" -r 8000 -c 1 -b 8 -e u-law "$outputFile"
}

Write-Host "Batch conversion complete. Files are in the '$outputDir' folder."
